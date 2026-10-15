"""SQLite storage: schema, migrations and typed accessors (spec §4).

All access happens on the main thread. Fetchers get a read-only `StoreView` snapshot instead.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .fetchers.base import (
    ApiSnapshot,
    ApiUsage,
    CacheEntry,
    FetchResult,
    Headline,
    SearchRow,
    StoreView,
    UploadsState,
    VideoObs,
    WikiDay,
)
from .timeutil import day_str, iso, parse_day, parse_iso

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed')), published INTEGER NOT NULL DEFAULT 0,
  cold_start INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS source_runs (run_id INTEGER NOT NULL REFERENCES runs, source_id TEXT NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT NOT NULL, units_ok INTEGER, units_total INTEGER, detail TEXT, error TEXT,
  PRIMARY KEY (run_id, source_id));
CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, artist_id TEXT, title TEXT NOT NULL,
  url TEXT NOT NULL, kind TEXT NOT NULL, published_at TEXT NOT NULL, thumbnail_url TEXT,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS videos_artist ON videos(artist_id, published_at);
CREATE TABLE IF NOT EXISTS video_snapshots (video_id TEXT NOT NULL REFERENCES videos, observed_at TEXT NOT NULL,
  views INTEGER NOT NULL, source TEXT NOT NULL CHECK (source IN ('rss','api')), PRIMARY KEY (video_id, observed_at));
CREATE TABLE IF NOT EXISTS wiki_daily (lang TEXT NOT NULL, title TEXT NOT NULL, day TEXT NOT NULL, views INTEGER NOT NULL,
  fetched_at TEXT NOT NULL, PRIMARY KEY (lang, title, day));
CREATE TABLE IF NOT EXISTS headlines (headline_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, lang TEXT NOT NULL,
  title TEXT NOT NULL, url TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS headlines_pub ON headlines(published_at);
CREATE TABLE IF NOT EXISTS artist_scores (run_id INTEGER NOT NULL, artist_id TEXT NOT NULL, score REAL NOT NULL, rank INTEGER,
  korea_first INTEGER NOT NULL, components_json TEXT NOT NULL, PRIMARY KEY (run_id, artist_id));
CREATE TABLE IF NOT EXISTS published (run_id INTEGER PRIMARY KEY, generated_at TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS why_cache (key TEXT PRIMARY KEY, why TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_usage (api TEXT NOT NULL, day TEXT NOT NULL, units INTEGER NOT NULL,
  PRIMARY KEY (api, day));
CREATE TABLE IF NOT EXISTS search_signal (artist_id TEXT PRIMARY KEY, target TEXT NOT NULL, landed INTEGER NOT NULL,
  day TEXT, z REAL, ratio REAL, baseline_n INTEGER NOT NULL, reason TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS yt_uploads (channel_id TEXT PRIMARY KEY, playlist_id TEXT, unavailable_until TEXT,
  checked_at TEXT NOT NULL, method TEXT, searched_at TEXT);
"""
# Columns added to existing tables after their first release: (table, column, type). migrate() adds the missing
# ones in place, so a cached DB keeps its data and `schema_version` stays 1.
ADDED_COLUMNS = (
    ("yt_uploads", "method", "TEXT"),
    ("yt_uploads", "searched_at", "TEXT"),
)


@dataclass(frozen=True)
class VideoRow:
    video_id: str
    channel_id: str
    artist_id: str | None
    title: str
    url: str
    kind: str
    published_at: datetime
    thumbnail_url: str | None
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class HeadlineRow:
    headline_id: str
    source_id: str
    lang: str
    title: str
    url: str
    published_at: datetime
    first_seen_at: datetime


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ infrastructure

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def migrate(self) -> None:
        with self.tx() as c:
            for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
                c.execute(stmt)
            for table, column, kind in ADDED_COLUMNS:
                if column not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            row =c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) > SCHEMA_VERSION:
                raise RuntimeError(f"database schema {row['value']} is newer than this code ({SCHEMA_VERSION})")

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))

    def first_run_at(self) -> datetime | None:
        v = self.get_meta("first_run_at")
        return parse_iso(v) if v else None

    def ensure_first_run_at(self, now: datetime) -> datetime:
        existing = self.first_run_at()
        if existing is not None:
            return existing
        self.set_meta("first_run_at", iso(now))
        return now

    # ------------------------------------------------------------------ runs

    def insert_run(self, started_at: datetime) -> int:
        cur = self.conn.execute("INSERT INTO runs(started_at, status) VALUES (?, 'running')", (iso(started_at),))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, finished_at: datetime, published: bool, cold_start: bool) -> None:
        self.conn.execute(
            "UPDATE runs SET status=?, finished_at=?, published=?, cold_start=? WHERE run_id=?",
            (status, iso(finished_at), int(published), int(cold_start), run_id),
        )

    def insert_source_run(self, run_id: int, r: FetchResult) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO source_runs(run_id, source_id, status, started_at, finished_at, units_ok, units_total,"
            " detail, error) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, r.source_id, r.status, iso(r.started_at), iso(r.finished_at), r.units_ok, r.units_total,
             r.detail, "\n".join(r.errors) if r.errors else None),
        )

    def last_success_at(self, source_id: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(finished_at) AS t FROM source_runs WHERE source_id=? AND status IN ('ok','degraded')",
            (source_id,),
        ).fetchone()
        return parse_iso(row["t"]) if row and row["t"] else None

    # ------------------------------------------------------------------ videos

    def upsert_video(self, obs: VideoObs, kind: str, artist_id: str | None) -> None:
        seen = iso(obs.observed_at)
        self.conn.execute(
            "INSERT INTO videos(video_id, channel_id, artist_id, title, url, kind, published_at, thumbnail_url,"
            " first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, url=excluded.url, kind=excluded.kind,"
            " artist_id=excluded.artist_id, published_at=excluded.published_at,"
            " thumbnail_url=COALESCE(excluded.thumbnail_url, videos.thumbnail_url),"
            " last_seen_at=MAX(videos.last_seen_at, excluded.last_seen_at)",
            (obs.video_id, obs.channel_id, artist_id, obs.title, obs.url, kind, iso(obs.published_at),
             obs.thumbnail_url, seen, seen),
        )
        if obs.views is not None:
            self.insert_snapshot(obs.video_id, obs.observed_at, obs.views, "rss")

    def insert_snapshot(self, video_id: str, observed_at: datetime, views: int, source: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO video_snapshots(video_id, observed_at, views, source) VALUES (?,?,?,?)",
            (video_id, iso(observed_at), int(views), source),
        )

    def insert_api_snapshot(self, s: ApiSnapshot) -> None:
        exists = self.conn.execute("SELECT 1 FROM videos WHERE video_id=?", (s.video_id,)).fetchone()
        if not exists:
            return
        self.insert_snapshot(s.video_id, s.observed_at, s.views, "api")
        self.conn.execute("UPDATE videos SET last_seen_at=MAX(last_seen_at, ?) WHERE video_id=?",
                          (iso(s.observed_at), s.video_id))

    def videos_seen_since(self, since: datetime) -> list[VideoRow]:
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE last_seen_at >= ? OR published_at >= ? ORDER BY video_id",
            (iso(since), iso(since)),
        ).fetchall()
        return [_video_row(r) for r in rows]

    def update_video_attribution(self, video_id: str, kind: str, artist_id: str | None) -> None:
        self.conn.execute("UPDATE videos SET kind=?, artist_id=? WHERE video_id=?", (kind, artist_id, video_id))

    def scoring_videos(self, since: datetime) -> tuple[list[VideoRow], dict[str, list[tuple[datetime, int, str]]]]:
        """Scoring candidates and their snapshots as (observed_at, views, source), oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE artist_id IS NOT NULL AND kind != 'short' AND published_at >= ?"
            " ORDER BY video_id",
            (iso(since),),
        ).fetchall()
        videos = [_video_row(r) for r in rows]
        snaps: dict[str, list[tuple[datetime, int, str]]] = defaultdict(list)
        if videos:
            snap_rows = self.conn.execute(
                "SELECT s.video_id, s.observed_at, s.views, s.source FROM video_snapshots s"
                " JOIN videos v USING(video_id)"
                " WHERE v.artist_id IS NOT NULL AND v.kind != 'short' AND v.published_at >= ?"
                " ORDER BY s.video_id, s.observed_at",
                (iso(since),),
            ).fetchall()
            for r in snap_rows:
                snaps[r["video_id"]].append((parse_iso(r["observed_at"]), int(r["views"]), r["source"]))
        return videos, snaps

    def api_candidate_ids(self, now: datetime) -> tuple[str, ...]:
        return tuple(self.api_candidates_published(now))

    def api_candidates_published(self, now: datetime) -> dict[str, datetime]:
        rows = self.conn.execute(
            "SELECT video_id, published_at FROM videos WHERE artist_id IS NOT NULL AND kind != 'short'"
            " AND published_at >= ? ORDER BY video_id",
            (iso(now - timedelta(days=30)),),
        ).fetchall()
        return {r["video_id"]: parse_iso(r["published_at"]) for r in rows}

    def known_video_ids(self) -> tuple[frozenset[str], frozenset[str]]:
        """(every stored video id, every channel id that has at least one stored video)."""
        rows = self.conn.execute("SELECT video_id, channel_id FROM videos").fetchall()
        return frozenset(r["video_id"] for r in rows), frozenset(r["channel_id"] for r in rows)

    # ------------------------------------------------------------------ wiki

    def upsert_wiki_day(self, d: WikiDay, fetched_at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO wiki_daily(lang, title, day, views, fetched_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(lang, title, day) DO UPDATE SET views=excluded.views, fetched_at=excluded.fetched_at",
            (d.lang, d.title, day_str(d.day), int(d.views), iso(fetched_at)),
        )

    def wiki_latest_days(self) -> dict[tuple[str, str], date]:
        rows = self.conn.execute("SELECT lang, title, MAX(day) AS d FROM wiki_daily GROUP BY lang, title").fetchall()
        return {(r["lang"], r["title"]): parse_day(r["d"]) for r in rows}

    def wiki_last_fetches(self) -> dict[tuple[str, str], datetime]:
        rows = self.conn.execute("SELECT lang, title, MAX(fetched_at) AS f FROM wiki_daily GROUP BY lang, title").fetchall()
        return {(r["lang"], r["title"]): parse_iso(r["f"]) for r in rows}

    def wiki_series(self, since: date) -> dict[tuple[str, str], dict[date, int]]:
        rows = self.conn.execute(
            "SELECT lang, title, day, views FROM wiki_daily WHERE day >= ? ORDER BY lang, title, day", (day_str(since),)
        ).fetchall()
        out: dict[tuple[str, str], dict[date, int]] = defaultdict(dict)
        for r in rows:
            out[(r["lang"], r["title"])][parse_day(r["day"])] = int(r["views"])
        return dict(out)

    # ------------------------------------------------------------------ headlines

    def headline_ids(self) -> frozenset[str]:
        return frozenset(r["headline_id"] for r in self.conn.execute("SELECT headline_id FROM headlines"))

    def insert_headline(self, h: Headline) -> bool:
        first_seen = h.fetched_at
        published = min(h.pub_date, first_seen) if h.pub_date is not None else first_seen
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO headlines(headline_id, source_id, lang, title, url, published_at, first_seen_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (h.headline_id, h.source_id, h.lang, h.title, h.url, iso(published), iso(first_seen)),
        )
        return cur.rowcount > 0

    def earliest_headline_at(self, lang: str) -> datetime | None:
        """published_at of the oldest stored headline in `lang`: where our coverage of that language starts."""
        row = self.conn.execute("SELECT MIN(published_at) AS t FROM headlines WHERE lang=?", (lang,)).fetchone()
        return parse_iso(row["t"]) if row and row["t"] else None

    def headlines_since(self, since: datetime) -> list[HeadlineRow]:
        rows = self.conn.execute(
            "SELECT * FROM headlines WHERE published_at >= ? ORDER BY published_at, headline_id", (iso(since),)
        ).fetchall()
        return [HeadlineRow(r["headline_id"], r["source_id"], r["lang"], r["title"], r["url"],
                            parse_iso(r["published_at"]), parse_iso(r["first_seen_at"])) for r in rows]

    # ------------------------------------------------------------------ http cache

    def http_validators(self) -> dict[str, tuple[str | None, str | None]]:
        return {r["url"]: (r["etag"], r["last_modified"]) for r in self.conn.execute("SELECT * FROM http_cache")}

    def upsert_http_cache(self, e: CacheEntry) -> None:
        self.conn.execute(
            "INSERT INTO http_cache(url, etag, last_modified, fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, last_modified=excluded.last_modified,"
            " fetched_at=excluded.fetched_at",
            (e.url, e.etag, e.last_modified, iso(e.fetched_at)),
        )

    # ------------------------------------------------------------------ keyed-API quota ledger

    def add_api_usage(self, u: ApiUsage) -> None:
        if u.units <= 0:
            return
        self.conn.execute(
            "INSERT INTO api_usage(api, day, units) VALUES (?,?,?) "
            "ON CONFLICT(api, day) DO UPDATE SET units = api_usage.units + excluded.units",
            (u.api, day_str(u.day), int(u.units)),
        )

    def api_usage(self) -> dict[tuple[str, date], int]:
        return {(r["api"], parse_day(r["day"])): int(r["units"])
                for r in self.conn.execute("SELECT api, day, units FROM api_usage")}

    # ------------------------------------------------------------------ YouTube uploads playlists (spec §3.3)

    def upsert_uploads_state(self, u: UploadsState) -> None:
        self.conn.execute(
            "INSERT INTO yt_uploads(channel_id, playlist_id, unavailable_until, checked_at, method, searched_at)"
            " VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(channel_id) DO UPDATE SET playlist_id=excluded.playlist_id,"
            " unavailable_until=excluded.unavailable_until, checked_at=excluded.checked_at,"
            " method=excluded.method, searched_at=excluded.searched_at",
            (u.channel_id, u.playlist_id, iso(u.unavailable_until) if u.unavailable_until else None, iso(u.checked_at),
             u.method, iso(u.searched_at) if u.searched_at else None),
        )

    def uploads_states(self) -> dict[str, UploadsState]:
        def when(v: str | None) -> datetime | None:
            return parse_iso(v) if v else None

        return {r["channel_id"]: UploadsState(
            r["channel_id"], r["playlist_id"], when(r["unavailable_until"]), parse_iso(r["checked_at"]),
            r["method"], when(r["searched_at"]))
            for r in self.conn.execute("SELECT * FROM yt_uploads ORDER BY channel_id")}

    # ------------------------------------------------------------------ Korean search signal (derived only)

    def upsert_search_row(self, r: SearchRow) -> None:
        """One row per artist: only our latest derived values. Naver's ratio series is never written."""
        self.conn.execute(
            "INSERT INTO search_signal(artist_id, target, landed, day, z, ratio, baseline_n, reason, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(artist_id) DO UPDATE SET target=excluded.target,"
            " landed=excluded.landed, day=excluded.day, z=excluded.z, ratio=excluded.ratio,"
            " baseline_n=excluded.baseline_n, reason=excluded.reason, fetched_at=excluded.fetched_at",
            (r.artist_id, day_str(r.target), int(r.landed), day_str(r.day) if r.day else None, r.z, r.ratio,
             int(r.baseline_n), r.reason, iso(r.fetched_at)),
        )

    def search_rows(self) -> dict[str, SearchRow]:
        out: dict[str, SearchRow] = {}
        for r in self.conn.execute("SELECT * FROM search_signal ORDER BY artist_id"):
            out[r["artist_id"]] = SearchRow(
                artist_id=r["artist_id"], target=parse_day(r["target"]), landed=bool(r["landed"]),
                day=parse_day(r["day"]) if r["day"] else None, z=r["z"], ratio=r["ratio"],
                baseline_n=int(r["baseline_n"]), reason=r["reason"], fetched_at=parse_iso(r["fetched_at"]),
            )
        return out

    # ------------------------------------------------------------------ fetcher snapshot

    def view(self, now: datetime) -> StoreView:
        known, channels = self.known_video_ids()
        candidates = self.api_candidates_published(now)
        return StoreView(
            wiki_latest=self.wiki_latest_days(),
            wiki_fetched=self.wiki_last_fetches(),
            validators=self.http_validators(),
            headline_ids=self.headline_ids(),
            api_candidates=tuple(candidates),
            known_videos=known,
            channels_with_videos=channels,
            published_at=candidates,
            api_usage=self.api_usage(),
            search_state=self.search_rows(),
            uploads_state=self.uploads_states(),
        )

    # ------------------------------------------------------------------ scores / published

    def insert_artist_scores(self, run_id: int, rows: Iterable[tuple[str, float, int | None, bool, dict[str, Any]]]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO artist_scores(run_id, artist_id, score, rank, korea_first, components_json)"
            " VALUES (?,?,?,?,?,?)",
            [(run_id, aid, score, rank, int(kf), json.dumps(comp, ensure_ascii=False, sort_keys=True))
             for aid, score, rank, kf, comp in rows],
        )

    def insert_published(self, run_id: int, generated_at: datetime, payload_json: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO published(run_id, generated_at, payload_json) VALUES (?,?,?)",
                          (run_id, iso(generated_at), payload_json))

    def rank_basis(self, now: datetime, compare_hours: float) -> tuple[datetime, dict[str, Any]] | None:
        """Newest published row with generated_at ≤ now − compare_hours, else the oldest row (spec §5.7)."""
        cutoff = iso(now - timedelta(hours=compare_hours))
        row = self.conn.execute(
            "SELECT generated_at, payload_json FROM published WHERE generated_at <= ?"
            " ORDER BY generated_at DESC, run_id DESC LIMIT 1", (cutoff,),
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT generated_at, payload_json FROM published ORDER BY generated_at ASC, run_id ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {}
        return parse_iso(row["generated_at"]), payload

    # ------------------------------------------------------------------ why cache (LLM only)

    def why_cache_get(self, key: str, not_before: datetime) -> str | None:
        row = self.conn.execute("SELECT why FROM why_cache WHERE key=? AND created_at >= ?",
                                (key, iso(not_before))).fetchone()
        return row["why"] if row else None

    def why_cache_put(self, key: str, why: str, now: datetime) -> None:
        self.conn.execute(
            "INSERT INTO why_cache(key, why, created_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET why=excluded.why, created_at=excluded.created_at",
            (key, why, iso(now)),
        )

    # ------------------------------------------------------------------ retention

    def apply_retention(self, now: datetime) -> None:
        d35 = iso(now - timedelta(days=35))
        with self.tx() as c:
            c.execute("DELETE FROM video_snapshots WHERE observed_at < ?", (d35,))
            c.execute("DELETE FROM videos WHERE last_seen_at < ? AND published_at < ?", (d35, d35))
            c.execute("DELETE FROM video_snapshots WHERE video_id NOT IN (SELECT video_id FROM videos)")
            c.execute("DELETE FROM headlines WHERE published_at < ?", (iso(now - timedelta(days=30)),))
            c.execute("DELETE FROM wiki_daily WHERE day < ?", (day_str((now - timedelta(days=60)).date()),))
            d14 = iso(now - timedelta(days=14))
            c.execute("DELETE FROM artist_scores WHERE run_id IN (SELECT run_id FROM runs WHERE started_at < ?)", (d14,))
            c.execute("DELETE FROM source_runs WHERE run_id IN (SELECT run_id FROM runs WHERE started_at < ?)", (d14,))
            c.execute("DELETE FROM runs WHERE started_at < ?", (d14,))
            c.execute("DELETE FROM published WHERE run_id NOT IN"
                      " (SELECT run_id FROM published ORDER BY generated_at DESC, run_id DESC LIMIT 500)")
            c.execute("DELETE FROM why_cache WHERE created_at < ?", (iso(now - timedelta(days=1)),))
            # Keyed-API data (spec §4): YouTube API Services policy allows storing API data for at most 30 days
            # without a refresh; Naver-derived values are kept far below the strictest reading of Naver's terms.
            # API-only videos (found by playlist discovery, never in RSS) go first, while their api snapshots
            # still identify them; a keyless DB has no api snapshots, so this never touches it.
            d30 = iso(now - timedelta(days=30))
            c.execute("DELETE FROM videos WHERE last_seen_at < ? AND published_at < ?"
                      " AND video_id IN (SELECT video_id FROM video_snapshots WHERE source='api')"
                      " AND video_id NOT IN (SELECT video_id FROM video_snapshots WHERE source='rss')", (d30, d30))
            c.execute("DELETE FROM video_snapshots WHERE source='api' AND observed_at < ?", (d30,))
            c.execute("DELETE FROM video_snapshots WHERE video_id NOT IN (SELECT video_id FROM videos)")
            c.execute("DELETE FROM search_signal WHERE fetched_at < ?", (iso(now - timedelta(days=7)),))
            c.execute("DELETE FROM published WHERE generated_at < ?", (iso(now - timedelta(days=21)),))
            # Expired "search"/"empty" marks are probed again; rows that only say "use the derived id" go too.
            c.execute("DELETE FROM yt_uploads WHERE playlist_id IS NULL AND (unavailable_until IS NULL"
                      " OR unavailable_until < ?)", (iso(now),))
            c.execute("DELETE FROM api_usage WHERE day < ?", (day_str((now - timedelta(days=40)).date()),))


def _video_row(r: sqlite3.Row) -> VideoRow:
    return VideoRow(
        video_id=r["video_id"], channel_id=r["channel_id"], artist_id=r["artist_id"], title=r["title"], url=r["url"],
        kind=r["kind"], published_at=parse_iso(r["published_at"]), thumbnail_url=r["thumbnail_url"],
        first_seen_at=parse_iso(r["first_seen_at"]), last_seen_at=parse_iso(r["last_seen_at"]),
    )
