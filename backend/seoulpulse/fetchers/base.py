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
SOURCE_NAMES = {
    "youtube": "YouTube",
    "wikipedia": "Wikipedia",
    "yonhap": "Yonhap",
    "soompi": "Soompi",
    "koreaboo": "Koreaboo",
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
class CacheEntry:
    url: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime


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


@dataclass(frozen=True)
class StoreView:
    """Read-only snapshot of DB state taken in the main thread before fetchers start."""

    wiki_latest: dict[tuple[str, str], date] = field(default_factory=dict)
    wiki_fetched: dict[tuple[str, str], datetime] = field(default_factory=dict)
    validators: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    headline_ids: frozenset[str] = frozenset()
    api_candidates: tuple[str, ...] = ()

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
