"""A fake internet for pipeline tests: routes requests to fixture responses via httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from .conftest import read_fixture

NEWS_FIXTURES = {
    "www.soompi.com": "soompi.xml",
    "www.koreaboo.com": "koreaboo.xml",
    "www.yna.co.kr": "yonhap.xml",
}

Responder = Callable[[httpx.Request], httpx.Response]


def ok_feed(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=read_fixture("youtube_hybe_labels.xml"),
                          headers={"content-type": "text/xml; charset=UTF-8"})


def wiki_404(_: httpx.Request) -> httpx.Response:
    return httpx.Response(404, content=read_fixture("pageviews_404.json"))


def news_ok(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=read_fixture(NEWS_FIXTURES[req.url.host]),
                          headers={"content-type": "application/rss+xml; charset=UTF-8"})


class FakeNet:
    def __init__(self, youtube: Responder = ok_feed, wiki: Responder = wiki_404,
                 news: dict[str, Responder] | None = None):
        self.youtube = youtube
        self.wiki = wiki
        self.news = news or {}
        self.calls: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(str(req.url))
        host = req.url.host
        if host == "www.youtube.com" and req.url.path == "/feeds/videos.xml":
            return self.youtube(req)
        if host == "wikimedia.org":
            return self.wiki(req)
        if host in NEWS_FIXTURES:
            return self.news.get(host, news_ok)(req)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
