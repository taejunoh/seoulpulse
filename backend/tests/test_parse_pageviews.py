from __future__ import annotations

import logging
from datetime import date

import httpx

from seoulpulse.config import load_config
from seoulpulse.fetchers.base import RunContext, StoreView
from seoulpulse.fetchers.wikipedia import (
    WikipediaFetcher,
    encode_title,
    fetch_range,
    pageviews_url,
    parse_pageviews,
)
from seoulpulse.http import HttpClient
from seoulpulse.seed import Alias, Artist, Seed

from .conftest import FakeClock, read_fixture, utc


def test_parse_200_response():
    days = parse_pageviews(read_fixture("pageviews_aespa_en.json"), "en", "Aespa")
    assert [(d.day, d.views) for d in days] == [
        (date(2026, 10, 11), 8100), (date(2026, 10, 12), 7900), (date(2026, 10, 13), 13600)]
    assert {d.lang for d in days} == {"en"} and {d.title for d in days} == {"Aespa"}


def test_url_encoding_of_korean_and_parenthesised_titles():
    assert encode_title("Kiss of Life (group)") == "Kiss_of_Life_%28group%29"
    assert encode_title("세븐틴 (음악 그룹)") == "%EC%84%B8%EB%B8%90%ED%8B%B4_%28%EC%9D%8C%EC%95%85_%EA%B7%B8%EB%A3%B9%29"
    url = pageviews_url("en", "Kiss of Life (group)", date(2026, 9, 14), date(2026, 10, 13))
    assert url == ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
                   "Kiss_of_Life_%28group%29/daily/2026091400/2026101300")
    assert pageviews_url("ko", "아이유", date(2026, 10, 11), date(2026, 10, 13)).endswith(
        "/ko.wikipedia/all-access/user/%EC%95%84%EC%9D%B4%EC%9C%A0/daily/2026101100/2026101300")


def test_fetch_range_backfill_and_incremental():
    end = date(2026, 10, 13)
    assert fetch_range(None, end) == (date(2026, 9, 14), end)          # 30-day backfill
    assert fetch_range(date(2026, 10, 10), end) == (date(2026, 10, 8), end)
    assert fetch_range(end, end) is None                               # already current: skip


def _seed(*titles: tuple[str | None, str | None]) -> Seed:
    artists = tuple(
        Artist(id=f"a{i}", name=f"A{i}", name_ko=None, aliases=(Alias(f"Artist{i}", False),), youtube=(),
               wiki_en=en, wiki_ko=ko)
        for i, (en, ko) in enumerate(titles)
    )
    return Seed(artists=artists, channels=())


def _ctx(transport, seed, view=None):
    clock = FakeClock()
    http = HttpClient("SeoulPulse/test", transport=transport, sleep=clock.sleep, clock=clock, rand=lambda: 0.0)
    cfg = load_config({"SP_CONTACT": "https://example.invalid/t"}, warn=False)
    return RunContext(now=utc(2026, 10, 14, 9, 0, 0), http=http, seed=seed, config=cfg, store=view or StoreView(),
                      log=logging.getLogger("t"), monotonic=clock)


def test_404_is_a_unit_error_and_200_succeeds():
    ok_body = read_fixture("pageviews_aespa_en.json")
    missing = read_fixture("pageviews_404.json")

    def handler(req: httpx.Request) -> httpx.Response:
        if "/Aespa/" in req.url.raw_path.decode():
            return httpx.Response(200, content=ok_body)
        return httpx.Response(404, content=missing)

    seed = _seed(("Aespa", None), ("No Such Article", None))
    out = WikipediaFetcher().fetch(_ctx(httpx.MockTransport(handler), seed))
    assert out.result.units_total == 2 and out.result.units_ok == 1
    assert out.result.status == "degraded"
    assert any("no data/not found" in e for e in out.result.errors)
    assert len(out.wiki) == 3
    assert out.result.detail == "1/2 articles current (day 2026-10-13)"


def test_current_articles_make_no_requests():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url)
        return httpx.Response(500)

    seed = _seed(("Aespa", "Aespa"))
    view = StoreView(wiki_latest={("en", "Aespa"): date(2026, 10, 13), ("ko", "Aespa"): date(2026, 10, 13)})
    out = WikipediaFetcher().fetch(_ctx(httpx.MockTransport(handler), seed, view))
    assert calls == []
    assert out.result.status == "ok" and out.result.units_ok == 2


def test_rate_limit_stops_the_pass_and_defers_the_rest():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(429, headers={"retry-after": "5"}, content=b"too many")

    seed = _seed(("A", None), ("B", None), ("C", None))
    out = WikipediaFetcher().fetch(_ctx(httpx.MockTransport(handler), seed))
    assert len(calls) == 3                     # one article, 1 try + 2 retries, then stop
    assert out.result.status == "error"
    assert "rate limited" in out.result.detail


def test_repoll_throttle_when_only_yesterday_is_missing():
    """Daily data lands once (01-06 UTC); an article fetched <60 min ago that only lacks yesterday is not re-polled."""
    from datetime import timedelta

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, content=read_fixture("pageviews_aespa_en.json"))

    now = utc(2026, 10, 14, 9, 0, 0)
    seed = _seed(("Aespa", None), ("Other", None))
    view = StoreView(
        wiki_latest={("en", "Aespa"): date(2026, 10, 12), ("en", "Other"): date(2026, 10, 12)},
        wiki_fetched={("en", "Aespa"): now - timedelta(minutes=10), ("en", "Other"): now - timedelta(hours=2)},
    )
    out = WikipediaFetcher().fetch(_ctx(httpx.MockTransport(handler), seed, view))
    assert len(calls) == 1 and "/Other/" in calls[0]
    assert out.result.status == "ok" and out.result.units_ok == 2
