"""Wikipedia pageviews (spec §3.4). Keyless; backfills 30 days on first sight of an article.

Freshness: `end` (yesterday, UTC) is the newest day Wikimedia normally has published. When even the newest day we
hold trails it by more than `WIKI_MAX_LAG_D`, the scorer ignores the data (§5.3) and the source reads `degraded`,
naming the last day Wikimedia published, even though every request answered (an upstream delay, not our failure).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from urllib.parse import quote

from ..http import HttpError
from ..scoring import wiki_day_fresh, wiki_expected_day
from .base import FetchOutput, RunContext, WikiDay, make_result, multi_unit_status

PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/"
    "{article}/daily/{start}00/{end}00"
)
BACKFILL_DAYS = 29
REPOLL = timedelta(minutes=60)
LANGS = ("ko", "en")


def encode_title(title: str) -> str:
    return quote(title.replace(" ", "_"), safe="")


def pageviews_url(lang: str, title: str, start: date, end: date) -> str:
    return PAGEVIEWS_URL.format(lang=lang, article=encode_title(title),
                                start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))


def parse_pageviews(content: bytes, lang: str, title: str) -> list[WikiDay]:
    data = json.loads(content.decode("utf-8"))
    out: list[WikiDay] = []
    for item in data.get("items", []) or []:
        ts = str(item.get("timestamp", ""))
        try:
            day = datetime.strptime(ts[:8], "%Y%m%d").date()
            views = int(item.get("views"))
        except (TypeError, ValueError):
            continue
        out.append(WikiDay(lang=lang, title=title, day=day, views=views))
    return out


def fetch_range(latest: date | None, end: date) -> tuple[date, date] | None:
    """Return (start, end) to request, or None when the article is already current."""
    if latest is not None and latest >= end:
        return None
    start = end - timedelta(days=BACKFILL_DAYS) if latest is None else latest - timedelta(days=2)
    return start, end


def article_order(articles: list[tuple[str, str]], view) -> list[tuple[str, str]]:
    """Never-fetched articles first, then the stalest; ties by (lang, title). Keeps partial runs fair."""
    def key(a: tuple[str, str]):
        latest = view.wiki_latest_day(*a)
        return (0 if latest is None else 1, latest or date.min, a[0], a[1])
    return sorted(articles, key=key)


class WikipediaFetcher:
    source_id = "wikipedia"

    def fetch(self, ctx: RunContext) -> FetchOutput:
        started = ctx.observed()
        today = ctx.now.date()
        end = wiki_expected_day(today)
        articles: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for artist in ctx.seed.artists:
            for lang in LANGS:
                title = artist.wiki_title(lang)
                if title and (lang, title) not in seen:
                    seen.add((lang, title))
                    articles.append((lang, title))
        total = len(articles)
        ok = current = 0
        errors: list[str] = []
        days: list[WikiDay] = []
        latest_seen: date | None = None
        answered = 0               # articles Wikimedia answered (200) this run
        rate_limited = False
        ordered = article_order(articles, ctx.store)
        for i, (lang, title) in enumerate(ordered):
            if ctx.time_left() <= 0:
                errors.append(f"time budget reached; {total - i} articles deferred to the next run")
                break
            latest = ctx.store.wiki_latest_day(lang, title)
            rng = fetch_range(latest, end)
            if rng is None:
                ok += 1
                current += 1
                latest_seen = latest if latest_seen is None else max(latest_seen, latest)
                continue
            last_fetch = ctx.store.wiki_last_fetch(lang, title)
            if (latest is not None and latest >= end - timedelta(days=1) and last_fetch is not None
                    and ctx.now - last_fetch < REPOLL):
                # Only yesterday is missing and we asked recently: Wikimedia publishes daily data once
                # (01:00-06:00 UTC), so re-polling every run just burns rate limit.
                ok += 1
                latest_seen = latest if latest_seen is None else max(latest_seen, latest)
                continue
            url = pageviews_url(lang, title, *rng)
            try:
                resp = ctx.http.get(url)
            except HttpError as exc:
                errors.append(f"{lang}:{title}: {exc}")
                continue
            if resp.status == 404:
                errors.append(f"{lang}:{title}: no data/not found")
                continue
            if resp.status == 429:
                rate_limited = True
                errors.append(f"rate limited (429) after retries; {total - i} articles deferred to the next run")
                break
            if resp.status != 200:
                errors.append(f"{lang}:{title}: HTTP {resp.status}")
                continue
            try:
                got = parse_pageviews(resp.content, lang, title)
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(f"{lang}:{title}: bad JSON {exc}"[:200])
                continue
            ok += 1
            answered += 1
            days.extend(got)
            newest = max((d.day for d in got), default=latest)
            if newest is not None:
                latest_seen = newest if latest_seen is None else max(latest_seen, newest)
                if newest >= end:
                    current += 1
        status = multi_unit_status(ok, total)
        # The newest day held for any article, stored or fetched now (articles that failed this run included).
        held = [ctx.store.wiki_latest_day(*a) for a in articles] + [latest_seen]
        newest_held = max((d for d in held if d is not None), default=None)
        if newest_held is not None and not wiki_day_fresh(newest_held, today):
            # Nothing usable anywhere: every article trails yesterday by 2+ days. If Wikimedia answered and still
            # had nothing newer, the delay is upstream; the scorer ignores these days (§5.3).
            lead = f"no Wikimedia data after {newest_held.isoformat()}"
            detail = f"{lead} (upstream delay) · {total} articles" if answered else f"{lead} · {ok}/{total} articles"
            errors.insert(0, f"{lead}: newest day trails {end.isoformat()} by {(end - newest_held).days} days")
            if status == "ok":
                status = "degraded"
        else:
            day_label = (latest_seen or end).isoformat()
            detail = f"{current}/{total} articles current (day {day_label})"
        if rate_limited:
            detail += " · rate limited"
        result = make_result(self.source_id, status, started, ctx.observed(), ok, total, detail, errors)
        return FetchOutput(result=result, wiki=days)
