"""Source fetchers. Each implements `fetch(ctx) -> FetchOutput` and never raises."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    OPTIONAL_SOURCE_IDS,
    SOURCE_IDS,
    SOURCE_NAMES,
    Fetcher,
    FetchOutput,
    FetchResult,
    RunContext,
    StoreView,
)
from .naver_datalab import NaverFetcher
from .news_rss import NewsFetcher
from .wikipedia import WikipediaFetcher
from .youtube_rss import YouTubeFetcher

if TYPE_CHECKING:
    from ..config import Config


def configured_optional_sources(config: Config | None) -> tuple[str, ...]:
    """Key-gated sources whose credentials are present (spec §3.7)."""
    if config is None:
        return ()
    return tuple(sid for sid in OPTIONAL_SOURCE_IDS if sid == "naver" and config.naver_enabled)


def default_fetchers(config: Config | None = None) -> list[Fetcher]:
    fetchers: list[Fetcher] = [
        YouTubeFetcher(),
        WikipediaFetcher(),
        NewsFetcher("yonhap"),
        NewsFetcher("soompi"),
        NewsFetcher("koreaboo"),
    ]
    if "naver" in configured_optional_sources(config):
        fetchers.append(NaverFetcher())
    return fetchers


__all__ = [
    "OPTIONAL_SOURCE_IDS", "SOURCE_IDS", "SOURCE_NAMES", "Fetcher", "FetchOutput", "FetchResult", "RunContext",
    "StoreView", "NaverFetcher", "NewsFetcher", "WikipediaFetcher", "YouTubeFetcher", "configured_optional_sources",
    "default_fetchers",
]
