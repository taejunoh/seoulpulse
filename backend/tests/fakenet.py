"""A fake internet for pipeline tests: routes requests to fixture responses via httpx.MockTransport."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

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


_RANGE_RE = re.compile(r"/daily/(\d{8})00/(\d{8})00$")


def wiki_flat(views: int = 1000, through: date | None = None) -> Responder:
    """Pageviews for every article: a flat `views` per day over the requested range, ending at `through` when it
    is earlier than the requested end (Wikimedia's answer while it has not published the later days yet)."""
    def respond(req: httpx.Request) -> httpx.Response:
        m = _RANGE_RE.search(req.url.raw_path.decode())
        assert m, req.url
        start, end = (datetime.strptime(g, "%Y%m%d").date() for g in m.groups())
        if through is not None:
            end = min(end, through)
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if not days:
            return wiki_404(req)
        items = [{"timestamp": d.strftime("%Y%m%d00"), "views": views} for d in days]
        return httpx.Response(200, content=json.dumps({"items": items}).encode())
    return respond


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
