"""Fetcher contract (spec §3.2). Fetchers never write to the DB and never raise."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..config import Config
    from ..http import HttpClient
    from ..seed import Seed

SOURCE_IDS = ("youtube", "wikipedia", "yonhap", "soompi", "koreaboo")
# Key-gated sources. They exist (in the run, in source_runs and as a 6th `sources[]` entry) only when their
# credentials are configured, so a keyless run is byte-identical to one without this code (spec §3.7, §7).
OPTIONAL_SOURCE_IDS = ("naver",)
SOURCE_NAMES = {
    "youtube": "YouTube",
    "wikipedia": "Wikipedia",
    "yonhap": "Yonhap",
    "soompi": "Soompi",
    "koreaboo": "Koreaboo",
    "naver": "Naver DataLab",
}
MAX_ERRORS = 20
MAX_DETAIL = 80


@dataclass(frozen=True)
class VideoObs:
    video_id: str
    channel_id: str
    title: str
    url: str
    published_at: datetime
    views: int | None
    observed_at: datetime
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class WikiDay:
    lang: str
    title: str
    day: date
    views: int


@dataclass(frozen=True)
class Headline:
    headline_id: str
    source_id: str
    lang: str
    title: str
    url: str
    guid: str
    pub_date: datetime | None   # None when missing, unparseable or >10 min in the future
    fetched_at: datetime


@dataclass(frozen=True)
class ApiSnapshot:
    video_id: str
    observed_at: datetime
    views: int


@dataclass(frozen=True)
class ApiUsage:
    """Quota units spent against a keyed API on its own quota day (YouTube: Pacific time, Naver: KST)."""

    api: str             # "youtube" | "naver"
    day: date
    units: int


@dataclass(frozen=True)
class SearchRow:
    """Our derived Korean-search spike for one artist (spec §3.7). Naver's raw ratio series is never stored."""

    artist_id: str
    target: date           # the KST day requested as endDate
    landed: bool           # the response already contained data for `target`
    day: date | None       # D, the latest day with data (None when missing)
    z: float | None
    ratio: float | None    # x_D / baseline mean (dimensionless)
    baseline_n: int
    reason: str            # "" or why the signal is missing
    fetched_at: datetime


@dataclass(frozen=True)
class CacheEntry:
    url: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime


@dataclass(frozen=True)
class UploadsState:
    """Per-channel uploads-playlist knowledge for the YouTube API pass (spec §3.3).

    `playlist_id` is a resolved uploads playlist that differs from the derived `UU…` id (None: use the derived id);
    `unavailable_until` marks a channel API discovery skips until then (RSS continues).
    """

    channel_id: str
    playlist_id: str | None
    unavailable_until: datetime | None
    checked_at: datetime


@dataclass(frozen=True)
class FetchResult:
    source_id: str
    status: str            # ok | degraded | error | disabled
    started_at: datetime
    finished_at: datetime
    units_ok: int
    units_total: int
    detail: str
    errors: list[str]


@dataclass
class FetchOutput:
    result: FetchResult
    videos: list[VideoObs] = field(default_factory=list)
    wiki: list[WikiDay] = field(default_factory=list)
    headlines: list[Headline] = field(default_factory=list)
    api_snapshots: list[ApiSnapshot] = field(default_factory=list)
    http_cache: list[CacheEntry] = field(default_factory=list)
    search: list[SearchRow] = field(default_factory=list)
    api_usage: list[ApiUsage] = field(default_factory=list)
    uploads_state: list[UploadsState] = field(default_factory=list)


@dataclass(frozen=True)
class StoreView:
    """Read-only snapshot of DB state taken in the main thread before fetchers start."""

    wiki_latest: dict[tuple[str, str], date] = field(default_factory=dict)
    wiki_fetched: dict[tuple[str, str], datetime] = field(default_factory=dict)
    validators: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    headline_ids: frozenset[str] = frozenset()
    api_candidates: tuple[str, ...] = ()
    # YouTube Data API path (spec §3.3): what the DB already knows, to skip re-discovering it.
    known_videos: frozenset[str] = frozenset()
    channels_with_videos: frozenset[str] = frozenset()
    published_at: dict[str, datetime] = field(default_factory=dict)   # api_candidates -> published_at
    api_usage: dict[tuple[str, date], int] = field(default_factory=dict)
    search_state: dict[str, SearchRow] = field(default_factory=dict)
    uploads_state: dict[str, UploadsState] = field(default_factory=dict)

    def api_units_used(self, api: str, day: date) -> int:
        return self.api_usage.get((api, day), 0)

    def wiki_latest_day(self, lang: str, title: str) -> date | None:
        return self.wiki_latest.get((lang, title))

    def wiki_last_fetch(self, lang: str, title: str) -> datetime | None:
        return self.wiki_fetched.get((lang, title))

    def http_validators(self, url: str) -> tuple[str | None, str | None] | None:
        return self.validators.get(url)


@dataclass
class RunContext:
    now: datetime
    http: HttpClient
    seed: Seed
    config: Config
    store: StoreView
    log: logging.Logger
    monotonic: Callable[[], float] = time.monotonic
    deadline: float | None = None        # monotonic seconds
    started_mono: float | None = None

    def __post_init__(self) -> None:
        if self.started_mono is None:
            self.started_mono = self.monotonic()

    def observed(self) -> datetime:
        """Current time on the run clock: `now` plus elapsed monotonic time, whole seconds."""
        elapsed = max(0.0, self.monotonic() - (self.started_mono or 0.0))
        return self.now + timedelta(seconds=int(elapsed))

    def time_left(self) -> float:
        if self.deadline is None:
            return float("inf")
        return self.deadline - self.monotonic()


class Fetcher(Protocol):
    source_id: str

    def fetch(self, ctx: RunContext) -> FetchOutput: ...


def multi_unit_status(units_ok: int, units_total: int) -> str:
    if units_total <= 0 or units_ok >= units_total:
        return "ok"
    if units_ok * 2 >= units_total:
        return "degraded"
    return "error"


def clip_detail(detail: str) -> str:
    detail = " ".join(detail.split())
    return detail if len(detail) <= MAX_DETAIL else detail[: MAX_DETAIL - 1] + "…"


def make_result(source_id: str, status: str, started: datetime, finished: datetime,
                units_ok: int, units_total: int, detail: str, errors: list[str]) -> FetchResult:
    return FetchResult(
        source_id=source_id, status=status, started_at=started, finished_at=finished,
        units_ok=units_ok, units_total=units_total, detail=clip_detail(detail),
        errors=[e[:300] for e in errors[:MAX_ERRORS]],
    )


def error_output(source_id: str, started: datetime, finished: datetime, message: str) -> FetchOutput:
    return FetchOutput(result=make_result(source_id, "error", started, finished, 0, 1, message, [message]))
