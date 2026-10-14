"""Source fetchers. Each implements `fetch(ctx) -> FetchOutput` and never raises."""

from __future__ import annotations

from .base import SOURCE_IDS, SOURCE_NAMES, Fetcher, FetchOutput, FetchResult, RunContext, StoreView
from .news_rss import NewsFetcher
from .wikipedia import WikipediaFetcher
from .youtube_rss import YouTubeFetcher


def default_fetchers() -> list[Fetcher]:
    return [
        YouTubeFetcher(),
        WikipediaFetcher(),
        NewsFetcher("yonhap"),
        NewsFetcher("soompi"),
        NewsFetcher("koreaboo"),
    ]


__all__ = [
    "SOURCE_IDS", "SOURCE_NAMES", "Fetcher", "FetchOutput", "FetchResult", "RunContext", "StoreView",
    "NewsFetcher", "WikipediaFetcher", "YouTubeFetcher", "default_fetchers",
]
