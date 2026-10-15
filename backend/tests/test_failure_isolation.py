from __future__ import annotations

import json
import threading

import httpx
import jsonschema

from seoulpulse.pipeline import run_once

from .conftest import SCHEMA_PATH, FakeClock, utc
from .fakenet import FakeNet, ok_feed

NOW = utc(2026, 10, 14, 9, 0, 0)
BAD_CHANNEL = "UCweOkPb1wVVH0Q0Tlj4a5Pw"   # 1theK: this one feed returns 404
FIRST_CHANNEL = "UCLkAepWjdylmXSltofFvsYQ"  # BTS: the YouTube fetcher's first request


def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def run(config, net, **kw):
    clock = FakeClock()
    return run_once(config, now=NOW, transport=net.transport, sleep=clock.sleep, monotonic=clock,
                    rand=lambda: 0.0, deadline_sec=10**6, **kw)


def test_mixed_failures_still_publish_valid_json(config):
    def youtube(req: httpx.Request) -> httpx.Response:
        if req.url.params.get("channel_id") == BAD_CHANNEL:
            return httpx.Response(404)
        return ok_feed(req)

    def koreaboo_timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=req)

    net = FakeNet(youtube=youtube, wiki=lambda req: httpx.Response(500),
                  news={"www.koreaboo.com": koreaboo_timeout})
    outcome = run(config, net)
    assert outcome.exit_code == 0
    statuses = {sid: r.status for sid, r in outcome.results.items()}
    assert statuses["wikipedia"] == "error"
    assert statuses["youtube"] == "degraded"
    assert statuses["koreaboo"] == "error"
    assert statuses["yonhap"] == "ok" and statuses["soompi"] == "ok"
    assert outcome.status == "partial"
    assert "timeout after 2 retries" in outcome.results["koreaboo"].detail

    payload = json.loads(config.trends_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema())
    assert payload["items"], "items should be produced from YouTube + news"
    by_id = {s["id"]: s for s in payload["sources"]}
    assert [s["id"] for s in payload["sources"]] == ["youtube", "wikipedia", "yonhap", "soompi", "koreaboo"]
    assert by_id["koreaboo"]["status"] == "error" and by_id["koreaboo"]["last_success_at"] is None
    assert by_id["youtube"]["detail"] == "RSS 76/77 channels"
    assert by_id["youtube"]["last_success_at"] is not None


class WikiBurstInsideYouTubeRequest:
    """Forces the interleaving that made the test above flaky on CI, on every attempt, with no timing luck.

    The Wikipedia fetcher's requests (500s, so HttpClient backs off 1 s and then 4 s of fake time after them) may
    only run while the YouTube fetcher's first request is in flight on its own thread. Once YouTube moves on to
    its second channel, Wikipedia runs freely. The waits time out only so a broken run fails instead of hanging.
    """

    BURST = 30   # wiki requests per YouTube attempt: 10 articles, ~50 s of fake backoff on the wiki thread

    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.permits = 0
        self.free = False
        self.wiki_calls = 0
        self.first_channel_attempts = 0
        self.timed_out = False

    def wiki(self, req: httpx.Request) -> httpx.Response:
        with self.cv:
            if not self.cv.wait_for(lambda: self.free or self.permits > 0, timeout=10):
                self.timed_out = True
            self.permits = max(0, self.permits - 1)
            self.wiki_calls += 1
            self.cv.notify_all()
        return httpx.Response(500)

    def youtube(self, req: httpx.Request) -> httpx.Response:
        with self.cv:
            if req.url.params.get("channel_id") == FIRST_CHANNEL:
                self.first_channel_attempts += 1
                target = self.wiki_calls + self.BURST
                self.permits += self.BURST
                self.cv.notify_all()
                if not self.cv.wait_for(lambda: self.wiki_calls >= target, timeout=10):
                    self.timed_out = True
            else:
                self.free = True
                self.cv.notify_all()
        return ok_feed(req)


def test_request_in_flight_does_not_absorb_other_threads_fake_sleeps(config):
    """Regression for the backend-ci flake in test_mixed_failures_still_publish_valid_json.

    Fetchers run on their own threads but share one FakeClock. While that clock was a single counter, every
    thread's sleep() moved every other thread's time, so the Wikipedia fetcher's retry backoff (146 articles x 5 s)
    accrued inside whatever YouTube or news request was in flight and tripped HttpClient's 20 s per-attempt body
    budget, i.e. "timeout after 2 retries" for a feed that answered instantly. That happened whenever the GIL
    parked a fetcher mid-request while the CPU-bound Wikipedia thread ran, which the CI runner did routinely.
    """
    burst = WikiBurstInsideYouTubeRequest()
    outcome = run(config, FakeNet(youtube=burst.youtube, wiki=burst.wiki), sources=["youtube", "wikipedia"])
    assert not burst.timed_out
    assert burst.wiki_calls >= burst.BURST           # the wiki thread really did back off during the request
    assert burst.first_channel_attempts == 1          # ... and the request was neither timed out nor retried
    assert outcome.results["youtube"].status == "ok"
    assert outcome.results["youtube"].detail == "RSS 77/77 channels"
    assert outcome.results["wikipedia"].status == "error"


def test_fake_clock_sleeps_on_concurrent_threads_overlap():
    clock = FakeClock()
    seen: list[float] = []

    def worker() -> None:
        clock.sleep(5.0)
        seen.append(clock())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == [1005.0, 1005.0, 1005.0]   # each thread sees only its own sleep, as on a real clock
    assert clock() == 1000.0                  # and none of them moved the creating thread's time
    clock.sleep(2.0)
    assert clock() == clock.t == 1002.0       # the creating thread sleeps on the shared base, as before
    assert clock.sleeps == [5.0, 5.0, 5.0, 2.0]


def test_all_sources_failing_keeps_existing_file_and_exits_2(config):
    config.public_dir.mkdir(parents=True)
    config.trends_path.write_text('{"sentinel": true}\n', encoding="utf-8")

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=req)

    net = FakeNet(youtube=boom, wiki=boom, news={h: boom for h in ("www.soompi.com", "www.koreaboo.com",
                                                                    "www.yna.co.kr")})
    outcome = run(config, net)
    assert outcome.exit_code == 2
    assert outcome.status == "failed"
    assert not outcome.published
    assert config.trends_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert not (config.public_dir / "trends.json.tmp").exists()


def test_all_sources_failing_without_file_publishes_empty(config):
    def boom(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    net = FakeNet(youtube=boom, wiki=boom, news={h: boom for h in ("www.soompi.com", "www.koreaboo.com",
                                                                    "www.yna.co.kr")})
    outcome = run(config, net)
    assert outcome.exit_code == 2
    payload = json.loads(config.trends_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema())
    assert payload["items"] == []
    assert all(s["status"] == "error" for s in payload["sources"])


def test_disabled_sources_are_marked_and_run_still_publishes(config):
    net = FakeNet()
    outcome = run(config, net, sources=["youtube", "yonhap", "soompi"])
    assert outcome.exit_code == 0
    payload = json.loads(config.trends_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema())
    by_id = {s["id"]: s for s in payload["sources"]}
    assert by_id["wikipedia"]["status"] == "disabled"
    assert by_id["koreaboo"]["status"] == "disabled"
    assert not any("wikimedia.org" in c or "koreaboo" in c for c in net.calls)


def test_escaped_exception_becomes_error(config):
    class Exploding:
        source_id = "soompi"

        def fetch(self, ctx):
            raise ValueError("kaboom")

    from seoulpulse.fetchers import NewsFetcher, YouTubeFetcher

    clock = FakeClock()
    outcome = run_once(config, now=NOW, transport=FakeNet().transport, sleep=clock.sleep, monotonic=clock,
                       fetchers=[YouTubeFetcher(), NewsFetcher("yonhap"), Exploding()], deadline_sec=10**6,
                       sources=["youtube", "yonhap", "soompi"])
    assert outcome.exit_code == 0
    assert outcome.results["soompi"].status == "error"
    assert "kaboom" in outcome.results["soompi"].detail
    assert config.trends_path.exists()


def test_concurrent_run_is_skipped(config):
    from seoulpulse.pipeline import RunLock

    with RunLock(config.lock_path) as held:
        assert held.acquired
        outcome = run(config, FakeNet())
    assert outcome.exit_code == 0 and outcome.status == "skipped"
    assert not config.trends_path.exists()


def test_loop_survives_crashing_runs(config):
    from seoulpulse.pipeline import run_loop

    calls = []

    def flaky_run():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    clock = FakeClock()
    run_loop(config, 1800, run=flaky_run, sleep=clock.sleep, monotonic=clock, max_runs=3)
    assert len(calls) == 3
    assert clock.sleeps == [1800, 1800]


def test_loop_sleeps_at_least_60s(config):
    from seoulpulse.pipeline import run_loop

    clock = FakeClock()

    def slow_run():
        clock.t += 1790

    run_loop(config, 1800, run=slow_run, sleep=clock.sleep, monotonic=clock, max_runs=2)
    assert clock.sleeps == [60.0]
