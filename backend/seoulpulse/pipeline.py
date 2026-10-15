"""Run orchestration and failure isolation (spec §3.6)."""

from __future__ import annotations

import fcntl
import logging
import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from .config import Config
from .contract import load_schema, validate_payload
from .fetchers import (
    SOURCE_IDS,
    Fetcher,
    FetchOutput,
    RunContext,
    configured_optional_sources,
    default_fetchers,
)
from .fetchers.base import FetchResult, make_result
from .http import HttpClient
from .matching import AliasMatcher, classify_video
from .publish import SourceInfo, build_payload, dumps, item_metrics, write_atomic
from .scoring import (
    ArtistInfo,
    Mention,
    Snapshot,
    Video,
    apply_rank_changes,
    basis_ranks,
    is_cold_start,
    rank_scores,
    score_all,
    search_signal,
)
from .seed import Seed, SeedError, load_seed
from .store import Store
from .timeutil import utcnow
from .why import apply_llm_whys, template_why

log = logging.getLogger("seoulpulse.pipeline")

DEADLINE_SEC = 8 * 60
FETCH_GRACE_SEC = 120  # fetchers stop starting new units this long before the hard deadline
EXIT_OK, EXIT_CRASH, EXIT_ALL_FAILED = 0, 1, 2


# ---------------------------------------------------------------------------- locking


class RunLock:
    """Non-blocking flock on var/run.lock. `acquired` is False if another run holds it."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None
        self.acquired = False

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.acquired = False
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fh is not None:
            if self.acquired:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()


# ---------------------------------------------------------------------------- outcome


@dataclass
class RunOutcome:
    exit_code: int
    run_id: int | None = None
    status: str = "failed"
    items: int = 0
    published: bool = False
    cold_start: bool = True
    payload: dict | None = None
    results: dict[str, FetchResult] | None = None
    summary: str = ""
    contract_errors: list[str] | None = None


# ---------------------------------------------------------------------------- helpers


def attribute(channel_kind: str | None, owner: str | None, title: str, matcher: AliasMatcher) -> str | None:
    if channel_kind == "artist":
        return owner
    if channel_kind in ("label", "broadcaster"):
        return matcher.primary(title)
    return None


def run_fetchers(ctx: RunContext, fetchers: Sequence[Fetcher], deadline_sec: float,
                 *, wall: Callable[[], float] = time.monotonic) -> dict[str, FetchOutput]:
    """Run each fetcher in its own daemon thread and wait at most `deadline_sec` (wall clock).

    Daemon threads (instead of a ThreadPoolExecutor, whose non-daemon workers are joined at interpreter
    exit) guarantee that a hung fetcher can never keep the process alive past the hard deadline.
    Late results are discarded; unfinished fetchers count as `error`.
    """
    outputs: dict[str, FetchOutput] = {}
    if not fetchers:
        return outputs
    finished: dict[str, FetchOutput] = {}
    gate = threading.Lock()
    closed = False

    def worker(f: Fetcher) -> None:
        started = ctx.observed()
        try:
            out = f.fetch(ctx)
        except Exception as exc:  # noqa: BLE001 - escaped exception becomes status=error
            msg = f"{type(exc).__name__}: {exc}"[:200]
            if ctx.time_left() > 0:
                log.exception("fetcher %s crashed", f.source_id)
            else:
                log.warning("fetcher %s stopped after the deadline: %s", f.source_id, msg)
            out = FetchOutput(result=make_result(f.source_id, "error", started, ctx.observed(), 0, 1, msg, [msg]))
        with gate:
            if not closed:
                finished[f.source_id] = out

    threads = [(f, threading.Thread(target=worker, args=(f,), name=f"fetch-{f.source_id}", daemon=True))
               for f in fetchers]
    end = wall() + deadline_sec
    for _, t in threads:
        t.start()
    for _, t in threads:
        t.join(timeout=max(0.0, end - wall()))
    with gate:
        closed = True
        for f, _ in threads:
            out = finished.get(f.source_id)
            if out is None:
                msg = f"deadline exceeded after {int(deadline_sec)}s"
                out = FetchOutput(result=make_result(f.source_id, "error", ctx.now, ctx.observed(), 0, 1, msg, [msg]))
            outputs[f.source_id] = out
    return outputs


def persist_output(store: Store, run_id: int, out: FetchOutput, seed: Seed, matcher: AliasMatcher) -> FetchResult:
    """Persist one source's output in its own transaction. Failure downgrades that source to error."""
    result = out.result
    try:
        with store.tx():
            channels = seed.channel_by_id
            for v in out.videos:
                ch = channels.get(v.channel_id)
                kind = classify_video(v.title, v.url, ch.kind if ch else None)
                artist = attribute(ch.kind if ch else None, ch.owner if ch else None, v.title, matcher)
                store.upsert_video(v, kind, artist)
            for s in out.api_snapshots:
                store.insert_api_snapshot(s)
            for d in out.wiki:
                store.upsert_wiki_day(d, result.finished_at)
            for h in out.headlines:
                store.insert_headline(h)
            for e in out.http_cache:
                store.upsert_http_cache(e)
            for r in out.search:
                store.upsert_search_row(r)
            for u in out.api_usage:
                store.add_api_usage(u)
            for st in out.uploads_state:
                store.upsert_uploads_state(st)
            store.insert_source_run(run_id, result)
    except Exception as exc:  # noqa: BLE001 - one source's persist failure must not affect others
        log.exception("persisting %s failed", result.source_id)
        msg = f"persist failed: {type(exc).__name__}: {exc}"[:200]
        result = make_result(result.source_id, "error", result.started_at, result.finished_at,
                             0, max(1, result.units_total), msg, [msg, *result.errors])
        with store.tx():
            store.insert_source_run(run_id, result)
            for u in out.api_usage:          # quota already spent must stay booked even if the data was lost
                store.add_api_usage(u)
    return result


def reattribute(store: Store, seed: Seed, matcher: AliasMatcher, now: datetime) -> int:
    """Recompute kind and artist for videos seen in the last 30 days (alias fixes apply retroactively)."""
    changed = 0
    channels = seed.channel_by_id
    with store.tx():
        for row in store.videos_seen_since(now - timedelta(days=30)):
            ch = channels.get(row.channel_id)
            kind = classify_video(row.title, row.url, ch.kind if ch else None)
            artist = attribute(ch.kind if ch else None, ch.owner if ch else None, row.title, matcher)
            if kind != row.kind or artist != row.artist_id:
                store.update_video_attribution(row.video_id, kind, artist)
                changed += 1
    return changed


def load_scoring_inputs(store: Store, seed: Seed, matcher: AliasMatcher, now: datetime):
    rows, snaps = store.scoring_videos(now - timedelta(days=30))
    channels = seed.channel_by_id
    videos_by_artist: dict[str, list[Video]] = defaultdict(list)
    for r in rows:
        ch = channels.get(r.channel_id)
        videos_by_artist[r.artist_id].append(Video(
            video_id=r.video_id, artist_id=r.artist_id, kind=r.kind, title=r.title, published_at=r.published_at,
            snapshots=tuple(Snapshot(t, v, src) for t, v, src in snaps.get(r.video_id, [])), url=r.url,
            channel_id=r.channel_id, channel_name=ch.display_name if ch else "",
        ))
    wiki = store.wiki_series((now - timedelta(days=20)).date())
    mentions_by_artist: dict[str, list[Mention]] = defaultdict(list)
    for h in store.headlines_since(now - timedelta(days=9)):
        for aid in sorted(matcher.mentions(h.title)):
            mentions_by_artist[aid].append(Mention(aid, h.headline_id, h.source_id, h.lang, h.title, h.url,
                                                   h.published_at))
    return videos_by_artist, wiki, mentions_by_artist


def artist_infos(seed: Seed) -> list[ArtistInfo]:
    return [ArtistInfo(a.id, a.name, a.name_ko, a.wiki_en, a.wiki_ko) for a in seed.artists]


# ---------------------------------------------------------------------------- main entry


def run_once(
    config: Config,
    *,
    now: datetime | None = None,
    sources: Iterable[str] | None = None,
    publish: bool = True,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    rand: Callable[[], float] = random.random,
    fetchers: Sequence[Fetcher] | None = None,
    deadline_sec: float = DEADLINE_SEC,
    llm_call=None,
) -> RunOutcome:
    with RunLock(config.lock_path) as lock:
        if not lock.acquired:
            log.info("another run holds %s; exiting", config.lock_path)
            return RunOutcome(exit_code=EXIT_OK, status="skipped", summary="skipped: locked")
        try:
            seed = load_seed(config.data_dir)
        except SeedError as exc:
            log.error("invalid seed: %s", exc)
            return RunOutcome(exit_code=EXIT_CRASH, summary=f"invalid seed: {exc}")
        for w in seed.warnings:
            log.debug("seed: %s", w)
        try:
            store = Store(config.db_path)
        except Exception as exc:  # noqa: BLE001
            log.exception("cannot open database")
            return RunOutcome(exit_code=EXIT_CRASH, summary=f"db error: {exc}")
        try:
            return _run(config, store, seed, now=now, sources=sources, publish=publish, transport=transport,
                        sleep=sleep, monotonic=monotonic, rand=rand, fetchers=fetchers,
                        deadline_sec=deadline_sec, llm_call=llm_call)
        finally:
            store.close()


def _run(config: Config, store: Store, seed: Seed, *, now, sources, publish, transport, sleep, monotonic, rand,
         fetchers, deadline_sec, llm_call) -> RunOutcome:
    t0 = monotonic()
    now = (now or utcnow()).replace(microsecond=0)
    optional = configured_optional_sources(config)          # key-gated sources whose credentials are set
    all_ids = SOURCE_IDS + optional
    enabled = set(all_ids) if sources is None else {s for s in sources if s in all_ids}
    store.ensure_first_run_at(now)
    first_run_at = store.first_run_at()
    run_id = store.insert_run(now)
    try:
        matcher = AliasMatcher(seed.artists)
        http = HttpClient(config.user_agent, transport=transport, sleep=sleep, clock=monotonic, rand=rand,
                          deadline=t0 + deadline_sec)
        soft = max(deadline_sec * 0.5, deadline_sec - FETCH_GRACE_SEC)
        ctx = RunContext(now=now, http=http, seed=seed, config=config, store=store.view(now), log=log,
                         monotonic=monotonic, deadline=t0 + soft, started_mono=t0)
        active = [f for f in (fetchers if fetchers is not None else default_fetchers(config))
                  if f.source_id in enabled]
        try:
            outputs = run_fetchers(ctx, active, deadline_sec)
        finally:
            http.close()

        results: dict[str, FetchResult] = {}
        for sid in all_ids:
            if sid in outputs:
                results[sid] = persist_output(store, run_id, outputs[sid], seed, matcher)
            else:
                status = "disabled" if sid not in enabled else "error"
                detail = "disabled for this run" if status == "disabled" else "fetcher missing"
                results[sid] = make_result(sid, status, now, now, 0, 0, detail, [])
                with store.tx():
                    store.insert_source_run(run_id, results[sid])
        for sid in all_ids:
            r = results[sid]
            if r.errors:
                log.warning("%s %s: %s", sid, r.status, "; ".join(r.errors[:3]))

        reattribute(store, seed, matcher, now)
        videos_by_artist, wiki, mentions = load_scoring_inputs(store, seed, matcher, now)
        search = None
        if "naver" in optional:      # unconfigured: no search term at all, even if old rows linger
            search = {aid: search_signal(row, now) for aid, row in store.search_rows().items()}
        # No English headline stored yet means no English coverage at all: then no Korean mention can "lead".
        en_from = store.earliest_headline_at("en") or now
        scores = score_all(artist_infos(seed), videos_by_artist, wiki, mentions, now, first_run_at, search, en_from)
        ranked = rank_scores(scores)
        basis = store.rank_basis(now, config.rank_compare_hours)
        apply_rank_changes(ranked, basis_ranks(basis[1]) if basis else None)
        for s in scores:
            s.why, rule = template_why(s, now)
            s.extra["why_rule"] = rule
        top = ranked[: config.top_n]
        metrics = {s.id: item_metrics(s) for s in top}
        apply_llm_whys(top, metrics, config, store, now, call=llm_call)
        cold = is_cold_start(scores)

        with store.tx():
            store.insert_artist_scores(run_id, [(s.id, s.score, s.rank, s.korea_first, s.components()) for s in scores])

        # "Every source failed" looks at the five keyless sources only: a key-gated enrichment alone cannot
        # justify replacing the last good file. Its own failure still makes the run "partial".
        core_results = [results[s] for s in SOURCE_IDS if s in enabled]
        all_failed = bool(core_results) and all(r.status == "error" for r in core_results)
        enabled_results = [results[s] for s in all_ids if s in enabled]
        run_status = "failed" if all_failed else (
            "ok" if all(r.status == "ok" for r in enabled_results) else "partial")

        source_info = {
            sid: SourceInfo(sid, results[sid].status, store.last_success_at(sid), results[sid].detail)
            for sid in all_ids
        }
        payload = build_payload(
            now=now, interval_sec=config.interval_sec, ranked=ranked, top_n=config.top_n,
            rank_basis_at=basis[0] if basis else None, cold_start=cold, sources=source_info,
            channel_urls={s.id: seed.artist_channel_url(s.id) for s in top},
            include_search="naver" in optional,
        )
        contract_errors = validate_payload(payload, load_schema(config.docs_dir))
        if contract_errors:
            log.error("payload violates docs/trends.schema.json (%d problem(s)); keeping the last good file: %s",
                      len(contract_errors), "; ".join(contract_errors[:5]))
            if run_status == "ok":
                run_status = "partial"
        trends = config.trends_path
        published = False
        if not publish:
            log.info("--no-publish: not writing %s", trends)
        elif contract_errors:
            pass  # never replace the last good file with a payload that breaks the contract
        elif all_failed and trends.exists():
            log.error("every source failed; keeping existing %s", trends)
        elif not payload["items"] and trends.exists():
            log.warning("zero eligible items; keeping existing %s", trends)
        else:
            text = dumps(payload)
            write_atomic(trends, text)
            with store.tx():
                store.insert_published(run_id, now, text)
            published = True

        store.apply_retention(now)
        exit_code = EXIT_ALL_FAILED if all_failed else EXIT_OK
        with store.tx():
            store.finish_run(run_id, run_status, now + timedelta(seconds=int(monotonic() - t0)), published, cold)
        elapsed = monotonic() - t0
        summary = (f"run {run_id} {run_status} items={len(payload['items'])} "
                   + " ".join(f"{sid}={results[sid].status}" for sid in all_ids)
                   + (" contract=INVALID" if contract_errors else "") + f" {elapsed:.1f}s")
        log.info(summary)
        return RunOutcome(exit_code=exit_code, run_id=run_id, status=run_status, items=len(payload["items"]),
                          published=published, cold_start=cold, payload=payload, results=results, summary=summary,
                          contract_errors=contract_errors)
    except Exception:
        log.exception("run %s crashed", run_id)
        try:
            with store.tx():
                store.finish_run(run_id, "failed", utcnow(), False, True)
        except Exception:  # noqa: BLE001
            pass
        return RunOutcome(exit_code=EXIT_CRASH, run_id=run_id, summary=f"run {run_id} crashed")


def run_loop(config: Config, interval: int, *, run: Callable[[], RunOutcome] | None = None,
             sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
             max_runs: int | None = None) -> None:
    """Run forever; one failed run never stops the loop."""
    n = 0
    while max_runs is None or n < max_runs:
        n += 1
        t0 = monotonic()
        try:
            (run or (lambda: run_once(config)))()
        except Exception:  # noqa: BLE001
            log.exception("run crashed; continuing loop")
        except KeyboardInterrupt:
            raise
        elapsed = monotonic() - t0
        if max_runs is not None and n >= max_runs:
            break
        sleep(max(60.0, interval - elapsed))

