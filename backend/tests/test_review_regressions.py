"""Regression tests for the six issues found in the second-opinion review (2026-10-14)."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import httpx
import jsonschema
import pytest
import yaml

from seoulpulse.contract import PACKAGED_SCHEMA, SchemaError, load_schema, validate_payload
from seoulpulse.fetchers.base import Headline
from seoulpulse.fetchers.news_rss import FeedFormatError, parse_rss, resolve_link
from seoulpulse.fetchers.youtube_rss import FeedFormatError as AtomFormatError
from seoulpulse.fetchers.youtube_rss import parse_youtube_feed
from seoulpulse.http import HttpClient, HttpError
from seoulpulse.matching import AliasMatcher
from seoulpulse.pipeline import run_fetchers, run_once
from seoulpulse.seed import load_seed
from seoulpulse.store import Store

from .conftest import DATA_DIR, EXAMPLE_PATH, REPO_DIR, SCHEMA_PATH, FakeClock, utc
from .fakenet import FakeNet

NOW = utc(2026, 10, 14, 9, 0, 0)
FETCHED = utc(2026, 10, 14, 8, 20, 0)
UA = "SeoulPulse/0.1 (+https://example.invalid/tests)"


def rss(items: str, channel_extra: str = "") -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>T</title>'
            f"<link>https://www.soompi.com/</link>{channel_extra}{items}</channel></rss>").encode()


def item(title: str, link: str, guid: str | None = None) -> str:
    g = f"<guid>{guid}</guid>" if guid else ""
    return (f"<item><title>{title}</title><link>{link}</link>{g}"
            "<pubDate>Wed, 14 Oct 2026 08:00:00 +0000</pubDate></item>")


CONSENT_XHTML = (b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
                 b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Before you continue</title></head>'
                 b"<body><form action='https://consent.example.invalid/save'>Accept all</form></body></html>")


# ----------------------------------------------------------------------------------------------- 1


class TestFinding1RelativeLinksAndContractGate:
    def test_relative_and_protocol_relative_links_resolve_against_feed_url(self):
        items = parse_rss(rss(item("[Fixture] aespa A", "/article/123/fixture-a")
                              + item("[Fixture] aespa B", "//www.soompi.com/article/124/b")
                              + item("[Fixture] aespa C", "article/125/c")),
                          "soompi", "en", FETCHED)
        assert [h.url for h in items] == [
            "https://www.soompi.com/article/123/fixture-a",
            "https://www.soompi.com/article/124/b",
            "https://www.soompi.com/article/125/c",
        ]

    def test_unsupported_schemes_are_dropped(self):
        items = parse_rss(rss(item("[Fixture] js", "javascript:alert(1)")
                              + item("[Fixture] mail", "mailto:desk@example.invalid")
                              + item("[Fixture] ftp", "ftp://files.example.invalid/x")
                              + item("[Fixture] ok", "https://www.soompi.com/article/9/ok")),
                          "soompi", "en", FETCHED)
        assert [h.title for h in items] == ["[Fixture] ok"]

    def test_resolve_link_uses_final_url_base(self):
        assert resolve_link("/view/AKR1", "https://www.yna.co.kr/rss/entertainment.xml") == \
            "https://www.yna.co.kr/view/AKR1"
        assert resolve_link("", "https://www.yna.co.kr/") is None

    def test_packaged_schema_matches_normative_docs_copy(self):
        assert PACKAGED_SCHEMA.read_bytes() == SCHEMA_PATH.read_bytes()

    def test_internal_validator_agrees_with_jsonschema(self):
        schema = load_schema(REPO_DIR / "docs")
        example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        assert validate_payload(example, schema) == []
        mutations = [
            lambda d: d["items"][0]["headlines"][0].__setitem__("url", "/relative/path"),
            lambda d: d["items"][0]["headlines"][0].__setitem__("url", "ftp://x.example/y"),
            lambda d: d["items"][0].__setitem__("why", "x" * 61),
            lambda d: d["items"][0].__setitem__("change", "sideways"),
            lambda d: d["items"][0]["metrics"].__setitem__("yt_accel", 9.0),
            lambda d: d["items"][0].__setitem__("extra_key", 1),
            lambda d: d.__setitem__("schema_version", 2),
            lambda d: d.__setitem__("cold_start", 1),
            lambda d: d["sources"].pop(),
            lambda d: d["items"][0]["top_video"].__setitem__("video_id", "short"),
            lambda d: d["items"][0].__setitem__("name_ko", None),            # valid: nullable
            lambda d: d["items"][0]["signals"].__setitem__("accel", -0.5),   # valid: boundary
        ]
        for mutate in mutations:
            doc = copy.deepcopy(example)
            mutate(doc)
            ours = validate_payload(doc, schema) == []
            theirs = jsonschema.Draft202012Validator(schema).is_valid(doc)
            assert ours == theirs, (doc.get("items", [{}])[0], ours, theirs)

    def test_validator_rejects_unknown_schema_keywords(self):
        with pytest.raises(SchemaError):
            validate_payload({}, {"type": "object", "patternProperties": {}})

    def test_invalid_payload_keeps_last_good_file_and_marks_run_partial(self, config):
        config.public_dir.mkdir(parents=True)
        config.trends_path.write_text('{"last": "good"}\n', encoding="utf-8")
        st = Store(config.db_path)
        with st.tx():
            # Bypass the parser: a stored headline with a non-http URL must never reach the published file.
            st.insert_headline(Headline("bad-link-0001", "yonhap", "ko", "[픽스처] 에스파 샘플 소식", "ftp://bad.example/x",
                                        "g-bad", NOW, NOW))
        st.close()
        clock = FakeClock()
        out = run_once(config, now=NOW, transport=FakeNet().transport, sleep=clock.sleep, monotonic=clock,
                       rand=lambda: 0.0, deadline_sec=10**6, sources=["yonhap", "soompi"])
        assert out.contract_errors and any("url" in e for e in out.contract_errors)
        assert out.published is False
        assert out.status == "partial"
        assert "contract=INVALID" in out.summary
        assert config.trends_path.read_text(encoding="utf-8") == '{"last": "good"}\n'
        assert not (config.public_dir / "trends.json.tmp").exists()


# ----------------------------------------------------------------------------------------------- 2


class _Blocking:
    source_id = "wikipedia"

    def __init__(self) -> None:
        self.release = threading.Event()

    def fetch(self, ctx):
        self.release.wait(60)
        raise RuntimeError("released")


class TestFinding2DeadlineBoundsExit:
    def test_trickling_body_hits_total_timeout(self):
        clock = FakeClock()
        calls = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(1)

            def trickle():
                for _ in range(100):
                    clock.t += 5          # each chunk arrives 5 s after the previous one (< read timeout)
                    yield b"x"

            return httpx.Response(200, content=trickle())

        c = HttpClient(UA, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock, rand=lambda: 0.0)
        with pytest.raises(HttpError, match="timeout after 2 retries"):
            c.get("https://www.soompi.com/feed")
        assert len(calls) == 3

    def test_run_deadline_stops_requests_and_retry_sleeps(self):
        clock = FakeClock()
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(503)

        c = HttpClient(UA, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                       rand=lambda: 0.0, deadline=clock.t + 2.0)
        with pytest.raises(HttpError, match="run deadline exceeded"):
            c.get("https://www.soompi.com/feed")
        assert len(calls) == 2                     # 1st try, 1 s backoff, 2nd try; the 4 s backoff would cross it
        assert clock.t <= 1000 + 2.0 + 1e-9
        clock.t += 10
        with pytest.raises(HttpError, match="run deadline exceeded"):
            c.get("https://www.soompi.com/other")
        assert len(calls) == 2                     # nothing is sent after the deadline

    def test_hung_fetcher_does_not_block_run_fetchers(self):
        import logging

        from seoulpulse.fetchers.base import RunContext, StoreView

        blocker = _Blocking()
        ctx = RunContext(now=NOW, http=None, seed=None, config=None, store=StoreView(),  # type: ignore[arg-type]
                         log=logging.getLogger("t"))
        t0 = time.monotonic()
        out = run_fetchers(ctx, [blocker], 0.3)
        assert time.monotonic() - t0 < 2.0
        assert out["wikipedia"].result.status == "error"
        assert "deadline exceeded" in out["wikipedia"].result.detail
        workers = [t for t in threading.enumerate() if t.name == "fetch-wikipedia"]
        assert workers and all(t.daemon for t in workers)
        blocker.release.set()

    def test_process_exits_promptly_with_a_hung_fetcher(self, tmp_path):
        script = tmp_path / "hang.py"
        script.write_text(textwrap.dedent(f"""
            import logging, sys, threading
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
            from datetime import datetime, UTC
            from seoulpulse.fetchers.base import RunContext, StoreView
            from seoulpulse.pipeline import run_fetchers

            class Hang:
                source_id = "youtube"
                def fetch(self, ctx):
                    threading.Event().wait(120)

            ctx = RunContext(now=datetime.now(UTC), http=None, seed=None, config=None, store=StoreView(),
                             log=logging.getLogger("t"))
            out = run_fetchers(ctx, [Hang()], 0.5)
            print(out["youtube"].result.status)
        """), encoding="utf-8")
        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "error"
        assert time.monotonic() - t0 < 15


# ----------------------------------------------------------------------------------------------- 3


def test_finding3_cache_keys_include_run_attempt():
    wf = yaml.safe_load((REPO_DIR / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8"))
    steps = {s.get("name"): s for s in wf["jobs"]["run"]["steps"]}
    restore, save = steps["Restore pipeline state"], steps["Save pipeline state"]
    key = "sp-state-${{ github.run_id }}-${{ github.run_attempt }}"
    assert restore["with"]["key"] == key
    assert save["with"]["key"] == key
    assert restore["with"]["restore-keys"].strip() == "sp-state-"
    assert save.get("if") == "always()"


# ----------------------------------------------------------------------------------------------- 4


class TestFinding4NonFeedBodies:
    def test_html_page_is_not_an_rss_feed(self):
        with pytest.raises(FeedFormatError):
            parse_rss(CONSENT_XHTML, "soompi", "en", FETCHED)
        with pytest.raises(AtomFormatError):
            parse_youtube_feed(CONSENT_XHTML, FETCHED)

    def test_empty_but_valid_feeds_are_ok(self):
        assert parse_rss(rss(""), "soompi", "en", FETCHED) == []
        empty_atom = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
        assert parse_youtube_feed(empty_atom, FETCHED) == []

    def test_rss1_rdf_feeds_are_accepted(self):
        rdf = (b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
               b'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
               b'<channel rdf:about="https://www.soompi.com/"><title>T</title></channel>'
               b'<item rdf:about="https://www.soompi.com/a/1"><title>[Fixture] RDF item</title>'
               b"<link>https://www.soompi.com/a/1</link><dc:date>2026-10-14T08:00:00Z</dc:date></item>"
               b"</rdf:RDF>")
        items = parse_rss(rdf, "soompi", "en", FETCHED)
        assert [(h.title, h.url, h.pub_date) for h in items] == [
            ("[Fixture] RDF item", "https://www.soompi.com/a/1", utc(2026, 10, 14, 8, 0, 0))]

    def test_consent_page_marks_sources_error_and_caches_nothing(self, config):
        def consent(req):
            return httpx.Response(200, content=CONSENT_XHTML, headers={"etag": '"consent-page"'})

        net = FakeNet(youtube=consent, news={"www.soompi.com": consent})
        clock = FakeClock()
        out = run_once(config, now=NOW, transport=net.transport, sleep=clock.sleep, monotonic=clock,
                       rand=lambda: 0.0, deadline_sec=10**6, sources=["youtube", "soompi", "yonhap"])
        assert out.results["soompi"].status == "error"
        assert "not an RSS feed" in out.results["soompi"].detail
        assert out.results["youtube"].status == "error"
        assert out.results["yonhap"].status == "ok"
        st = Store(config.db_path)
        cached = {r["url"] for r in st.conn.execute("SELECT url FROM http_cache")}
        st.close()
        assert "https://www.soompi.com/feed" not in cached
        assert not any("youtube.com" in u for u in cached)

    def test_valid_empty_news_feed_reports_ok(self, config):
        net = FakeNet(news={"www.soompi.com": lambda req: httpx.Response(200, content=rss(""))})
        clock = FakeClock()
        out = run_once(config, now=NOW, transport=net.transport, sleep=clock.sleep, monotonic=clock,
                       deadline_sec=10**6, sources=["soompi"])
        assert out.results["soompi"].status == "ok"
        assert out.results["soompi"].detail == "0 items, 0 new"


# ----------------------------------------------------------------------------------------------- 5


@pytest.fixture(scope="module")
def matcher():
    return AliasMatcher(load_seed(DATA_DIR).artists)


class TestFinding5CommonWordAliases:
    def test_seventeen_requires_band_styling(self, matcher):
        assert "seventeen" not in matcher.mentions("[Fixture] Actress Recalls Her Debut At Seventeen")
        assert "seventeen" not in matcher.mentions("she moved to Seoul at seventeen")
        assert "seventeen" in matcher.mentions("[Fixture] SEVENTEEN Tops The Chart")
        assert "seventeen" in matcher.mentions("세븐틴, 월드투어 서울 공연 성료")

    def test_kickflip_and_all_day_project(self, matcher):
        assert "kickflip" not in matcher.mentions("Skater lands a perfect kickflip on live TV")
        assert "kickflip" in matcher.mentions("[Fixture] KickFlip Announces Comeback")
        assert "allday-project" not in matcher.mentions("Filming was an all day project for the cast")
        assert "allday-project" in matcher.mentions("[Fixture] ALL DAY PROJECT Wins On Music Bank")
        assert "allday-project" in matcher.mentions("[Fixture] ALLDAY PROJECT Drops New Single")

    def test_roster_still_valid(self):
        assert load_seed(DATA_DIR).issues == ()


# ----------------------------------------------------------------------------------------------- 6


class TestFinding6ManualRedirects:
    def _client(self, handler, clock, **kw):
        return HttpClient(UA, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                          rand=lambda: 0.0, **kw)

    def test_redirect_hops_are_paced_per_destination_host(self):
        clock = FakeClock()
        seen = []

        def handler(req):
            seen.append((req.url.host, clock.t))
            if req.url.host == "www.soompi.com":
                return httpx.Response(301, headers={"location": "https://wikimedia.org/final"})
            return httpx.Response(200, content=b"ok")

        c = self._client(handler, clock)
        c.get("https://wikimedia.org/warmup")          # sets wikimedia.org's last-request time
        r = c.get("https://www.soompi.com/feed")       # redirects to wikimedia.org right away
        assert r.status == 200 and r.content == b"ok" and r.url == "https://wikimedia.org/final"
        # The hop to wikimedia.org waited for that host's 0.4 s pacing interval.
        assert clock.sleeps == [pytest.approx(0.4)]
        assert [h for h, _ in seen] == ["wikimedia.org", "www.soompi.com", "wikimedia.org"]

    def test_body_cap_applies_on_redirect_hops(self):
        clock = FakeClock()

        def handler(req):
            if req.url.path == "/start":
                return httpx.Response(302, headers={"location": "/big"})
            return httpx.Response(200, content=b"x" * 4096)

        with pytest.raises(HttpError, match="exceeds|too large"):
            self._client(handler, clock, max_bytes=1024).get("https://www.soompi.com/start")

    def test_redirect_loops_and_bad_schemes_are_refused(self):
        clock = FakeClock()
        loop = self._client(lambda req: httpx.Response(302, headers={"location": req.url.path + "x"}), clock)
        with pytest.raises(HttpError, match="too many redirects"):
            loop.get("https://www.soompi.com/a")
        bad = self._client(lambda req: httpx.Response(302, headers={"location": "file:///etc/passwd"}), clock)
        with pytest.raises(HttpError, match="refusing redirect"):
            bad.get("https://www.soompi.com/a")

    def test_cookies_do_not_follow_cross_site_redirects(self):
        clock = FakeClock()
        seen = {}

        def handler(req):
            seen[req.url.host] = req.headers.get("cookie")
            if req.url.host == "www.youtube.com":
                return httpx.Response(302, headers={"location": "https://consent.youtube.com/m"})
            if req.url.host == "consent.youtube.com":
                return httpx.Response(302, headers={"location": "https://tracker.example.invalid/x"})
            return httpx.Response(200)

        self._client(handler, clock).get("https://www.youtube.com/@aespa", cookies={"CONSENT": "YES+1"})
        assert seen["www.youtube.com"] == "CONSENT=YES+1"
        assert seen["consent.youtube.com"] == "CONSENT=YES+1"
        assert seen["tracker.example.invalid"] is None
