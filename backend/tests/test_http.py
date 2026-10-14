from __future__ import annotations

import httpx
import pytest

from seoulpulse.http import HttpClient, HttpError, pace_interval, parse_retry_after, redact

from .conftest import FakeClock

UA = "SeoulPulse/0.1 (+https://example.invalid/tests)"


def client(handler, clock: FakeClock, **kw) -> HttpClient:
    return HttpClient(UA, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                      rand=lambda: 0.0, **kw)


def seq_handler(responses):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    return handler, calls


def test_503_then_200_is_retried(clock):
    handler, calls = seq_handler([httpx.Response(503), httpx.Response(200, content=b"ok")])
    r = client(handler, clock).get("https://www.soompi.com/feed")
    assert r.status == 200 and r.content == b"ok"
    assert len(calls) == 2
    assert 1.0 in clock.sleeps                    # first backoff is 1 s (+ jitter, 0 here)


def test_two_retries_max_then_returns_last_status(clock):
    handler, calls = seq_handler([httpx.Response(500)])
    r = client(handler, clock).get("https://www.soompi.com/feed")
    assert r.status == 500
    assert len(calls) == 3                        # 1 try + 2 retries
    backoffs = [s for s in clock.sleeps if s in (1.0, 4.0)]
    assert backoffs == [1.0, 4.0]


def test_retry_after_is_honored_with_fake_sleep(clock):
    handler, calls = seq_handler([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    r = client(handler, clock).get("https://wikimedia.org/api/x")
    assert r.status == 200
    assert 7.0 in clock.sleeps


def test_retry_after_is_capped_at_30s(clock):
    handler, _ = seq_handler([httpx.Response(429, headers={"Retry-After": "120"}), httpx.Response(200)])
    client(handler, clock).get("https://wikimedia.org/api/x")
    assert 30.0 in clock.sleeps and 120.0 not in clock.sleeps


def test_404_is_not_retried(clock):
    handler, calls = seq_handler([httpx.Response(404)])
    r = client(handler, clock).get("https://wikimedia.org/api/missing")
    assert r.status == 404 and len(calls) == 1
    assert clock.sleeps == []


def test_timeouts_are_retried_then_raise(clock):
    handler, calls = seq_handler([httpx.ReadTimeout("slow")])
    with pytest.raises(HttpError, match="timeout after 2 retries"):
        client(handler, clock).get("https://www.koreaboo.com/feed/")
    assert len(calls) == 3


def test_connect_errors_are_not_retried(clock):
    handler, calls = seq_handler([httpx.ConnectError("refused")])
    with pytest.raises(HttpError):
        client(handler, clock).get("https://www.koreaboo.com/feed/")
    assert len(calls) == 1


def test_user_agent_and_conditional_get(clock):
    handler, calls = seq_handler([httpx.Response(304)])
    r = client(handler, clock).get("https://www.soompi.com/feed", validators=('"abc"', "Wed, 14 Oct 2026 08:00:00 GMT"))
    assert r.status == 304
    req = calls[0]
    assert req.headers["User-Agent"] == UA
    assert req.headers["If-None-Match"] == '"abc"'
    assert req.headers["If-Modified-Since"] == "Wed, 14 Oct 2026 08:00:00 GMT"


def test_per_host_pacing(clock):
    handler, _ = seq_handler([httpx.Response(200)])
    c = client(handler, clock)
    c.get("https://wikimedia.org/a")
    c.get("https://wikimedia.org/b")
    c.get("https://www.soompi.com/feed")          # different host: no wait
    assert clock.sleeps == [pytest.approx(0.4)]
    assert pace_interval("www.youtube.com") == 0.5
    assert pace_interval("ko.wikipedia.org") == 1.0
    assert pace_interval("www.googleapis.com") == 0.2
    assert pace_interval("www.yna.co.kr") == 1.0


def test_response_cap(clock):
    handler, _ = seq_handler([httpx.Response(200, content=b"x" * 2048)])
    with pytest.raises(HttpError, match="exceeds|too large"):
        client(handler, clock, max_bytes=1024).get("https://www.soompi.com/feed")


def test_parse_retry_after_and_redact():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert redact("https://www.googleapis.com/youtube/v3/videos?part=statistics&key=SECRET123&id=a") == \
        "https://www.googleapis.com/youtube/v3/videos?part=statistics&key=REDACTED&id=a"
