"""Naver DataLab search trend → "Korean search spike" (spec §3.7). Off unless NAVER_CLIENT_ID and
NAVER_CLIENT_SECRET are both set; then it is source `naver`, a 6th entry in `sources[]`.

Endpoints. The request and response bodies are identical; host, path and auth headers differ:

* NAVER API HUB (Naver Cloud), the only place to get new credentials since 2026-07-31:
  `POST https://naverapihub.apigw.ntruss.com/search-trend/v1/search`, headers `X-NCP-APIGW-API-KEY-ID` /
  `X-NCP-APIGW-API-KEY`. Quota: 50,000 calls/month per app, 50 requests/s, 429 when exceeded.
* developers.naver.com (legacy keys issued by 2026-07-30, supported until 2027-06-30):
  `POST https://openapi.naver.com/v1/datalab/search`, headers `X-Naver-Client-Id` / `X-Naver-Client-Secret`.
  Quota: 1,000 calls/day.

`SP_NAVER_ENDPOINT=auto` (default) tries the HUB first and falls back to developers.naver.com when the HUB
rejects the credentials; `hub` or `developers` pins one.

Each request carries up to 5 keyword groups (one per artist, up to 20 keywords each) and asks for the last 28
KST days at daily granularity. Data is daily, so an artist is re-requested only when yesterday (KST) is not in
its last answer yet, at most every 2 hours. That is about 17 calls per day for 82 artists. Hard caps: 20 calls
per run and 300 per KST day (ledger `api_usage`), below both quotas even if the ledger were lost.

Storage (compliance by design): only our derived values (latest day D, z-score, x_D/μ ratio) are stored, one
row per artist, kept ≤7 days. Naver's ratio series lives only in memory during the fetch. The values never
reach the optional LLM, and the credentials never reach logs, errors, the DB or trends.json.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from ..http import HttpError, Response
from ..scoring import SEARCH_STALE_D, search_spike
from ..timeutil import kst_date
from .base import ApiUsage, FetchOutput, RunContext, SearchRow, make_result, multi_unit_status

HUB_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
DEVELOPERS_URL = "https://openapi.naver.com/v1/datalab/search"
ENDPOINTS = {
    "hub": (HUB_URL, "X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
    "developers": (DEVELOPERS_URL, "X-Naver-Client-Id", "X-Naver-Client-Secret"),
}
GROUPS_PER_REQUEST = 5
WINDOW_DAYS = 28
REPOLL = timedelta(hours=1, minutes=55)   # "every 2 hours", tolerant of run start jitter
RUN_CAP = 20
DAY_CAP = 300
AUTH_STATUSES = frozenset({401, 403})
MAX_CONSECUTIVE_FAILURES = 3   # an unreachable endpoint is not retried batch after batch


def endpoint_order(mode: str) -> list[str]:
    return {"hub": ["hub"], "developers": ["developers"]}.get(mode, ["hub", "developers"])


def request_body(groups: Sequence[tuple[str, Sequence[str]]], start: date, end: date) -> dict:
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "keywordGroups": [{"groupName": gid, "keywords": list(kw)} for gid, kw in groups],
    }


def parse_trend(content: bytes) -> dict[str, dict[date, float]]:
    """`results[].{title, data[].{period, ratio}}` → {groupName: {day: ratio}}."""
    data = json.loads(content.decode("utf-8"))
    out: dict[str, dict[date, float]] = {}
    for res in data.get("results", []) or []:
        title = res.get("title")
        if not isinstance(title, str):
            continue
        series: dict[date, float] = {}
        for point in res.get("data", []) or []:
            try:
                day = date.fromisoformat(str(point.get("period", ""))[:10])
                series[day] = float(point.get("ratio"))
            except (TypeError, ValueError):
                continue
        out[title] = series
    return out


def error_code(content: bytes) -> str:
    """A short machine code from a Naver error body (gateway, legacy or DataLab shape); never the raw text."""
    try:
        data = json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("error"), dict):
        data = data["error"]
    for key in ("errorCode", "errId", "code"):
        v = data.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()[:24]
    return ""


def needs_fetch(row: SearchRow | None, target: date, now: datetime) -> bool:
    if row is None or row.target < target:
        return True
    if row.target > target or row.landed:
        return False
    return now - row.fetched_at >= REPOLL


class _Fatal(Exception):
    pass


class NaverFetcher:
    source_id = "naver"

    def fetch(self, ctx: RunContext) -> FetchOutput:
        started = ctx.observed()
        cfg = ctx.config
        if not cfg.naver_enabled:
            return FetchOutput(result=make_result(self.source_id, "disabled", started, started, 0, 0,
                                                  "not configured", []))
        today = kst_date(ctx.now)
        target = today - timedelta(days=1)
        start = target - timedelta(days=WINDOW_DAYS - 1)
        artists = [(a.id, a.search_keywords()) for a in ctx.seed.artists if a.search_keywords()]
        state = dict(ctx.store.search_state)
        due = [(aid, kw) for aid, kw in artists if needs_fetch(state.get(aid), target, ctx.now)]
        used = ctx.store.api_units_used("naver", today)
        limit = min(RUN_CAP, max(0, DAY_CAP - used))

        rows: list[SearchRow] = []
        errors: list[str] = []
        flags: list[str] = []
        units = 0
        exhausted = False
        failed: set[str] = set()
        order = endpoint_order(cfg.naver_endpoint)
        locked: str | None = None

        def post(body: dict) -> Response | None:
            nonlocal units, locked, order, exhausted
            while order:
                name = locked or order[0]
                url, id_header, secret_header = ENDPOINTS[name]
                headers = {id_header: cfg.naver_client_id or "", secret_header: cfg.naver_client_secret or ""}
                units += 1
                try:
                    resp = ctx.http.post_json(url, body, headers=headers)
                except HttpError as exc:
                    errors.append(f"{name}: {exc}"[:200])
                    return None
                if resp.status == 200:
                    locked = name
                    return resp
                code = error_code(resp.content)
                errors.append(f"{name}: HTTP {resp.status} {code}".strip()[:200])
                if resp.status in AUTH_STATUSES and locked is None:
                    order = [n for n in order if n != name]     # credentials belong to the other endpoint?
                    if not order:
                        flags.append("credentials rejected")
                        raise _Fatal()
                    if units >= limit:
                        flags.append("run cap")
                        raise _Fatal()
                    continue
                if resp.status == 429:
                    exhausted = True
                    flags.append("quota exhausted")
                    raise _Fatal()
                return None
            raise _Fatal()

        streak = 0
        for i in range(0, len(due), GROUPS_PER_REQUEST):
            batch = due[i : i + GROUPS_PER_REQUEST]
            if streak >= MAX_CONSECUTIVE_FAILURES:
                flags.append("giving up after repeated failures")
                failed.update(aid for aid, _ in due[i:])
                break
            if units >= limit:
                flags.append("daily cap" if limit < RUN_CAP else "run cap")
                failed.update(aid for aid, _ in due[i:])
                break
            if ctx.time_left() <= 0:
                errors.append("deadline reached")
                failed.update(aid for aid, _ in due[i:])
                break
            try:
                resp = post(request_body(batch, start, target))
            except _Fatal:
                failed.update(aid for aid, _ in due[i:])
                break
            if resp is None:
                failed.update(aid for aid, _ in batch)
                streak += 1
                continue
            try:
                trend = parse_trend(resp.content)
            except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                errors.append(f"bad JSON {exc}"[:200])
                failed.update(aid for aid, _ in batch)
                streak += 1
                continue
            streak = 0
            landed = any(target in s for s in trend.values())
            fetched_at = ctx.observed()
            for aid, _ in batch:
                calc = search_spike(trend.get(aid, {}), target)
                row = SearchRow(artist_id=aid, target=target, landed=landed, day=calc.day,
                                z=calc.z, ratio=calc.ratio, baseline_n=calc.baseline_n, reason=calc.reason,
                                fetched_at=fetched_at)
                rows.append(row)
                state[aid] = row

        total = len(artists)
        ok = sum(1 for aid, _ in artists if aid not in failed)
        held = [r for aid, _ in artists if (r := state.get(aid)) is not None]
        current = sum(1 for r in held if r.target >= target and r.landed)
        # `day` is the newest KST day with data (D), not the requested target: before DataLab publishes
        # yesterday (KST morning) every row still ends the day before, which reads "0/82 current · day <target−1>".
        newest = max((r.day for r in held if r.day is not None), default=None)
        ledger = max(units, DAY_CAP - used) if exhausted else units
        calls = f"{units} calls ({used + ledger}/{DAY_CAP} today)"
        status = multi_unit_status(ok, total)
        if newest is not None and newest < target - timedelta(days=SEARCH_STALE_D):
            # Every row is past the scorer's window (§5.3): Naver contributes nothing. If DataLab answered for
            # this target and still had nothing newer, the delay is upstream.
            lead = f"no DataLab data after {newest.isoformat()}"
            asked = any(r.target >= target for r in held)
            detail = f"{lead}{' (upstream delay)' if asked else ''} · {calls}"
            errors.insert(0, f"{lead}: newest day trails {target.isoformat()} by {(target - newest).days} days")
            if status == "ok":
                status = "degraded"
        else:
            detail = f"{current}/{total} current · day {(newest or target).isoformat()} · {calls}"
        if locked == "developers":
            detail += " · legacy endpoint"
        for f in dict.fromkeys(flags):
            detail += f" · {f}"
        usage = [ApiUsage("naver", today, ledger)] if ledger > 0 else []
        result = make_result(self.source_id, status, started, ctx.observed(), ok, total, detail, errors)
        return FetchOutput(result=result, search=rows, api_usage=usage)
