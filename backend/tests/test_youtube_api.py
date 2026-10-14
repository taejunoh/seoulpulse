from __future__ import annotations

import json
from dataclasses import replace

import httpx

from seoulpulse.pipeline import run_once

from .conftest import FakeClock, utc
from .fakenet import FakeNet, ok_feed

NOW = utc(2026, 10, 14, 9, 0, 0)


def test_api_pass_runs_only_with_key_and_redacts_it(config):
    seen = []

    def youtube(req: httpx.Request) -> httpx.Response:
        return ok_feed(req)

    net = FakeNet(youtube=youtube)
    base = net.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "www.googleapis.com":
            seen.append(req)
            ids = req.url.params["id"].split(",")
            body = {"items": [{"id": i, "statistics": {"viewCount": "777"}} for i in ids]}
            return httpx.Response(200, content=json.dumps(body).encode())
        return base(req)

    cfg = replace(config, youtube_api_key="SECRET-KEY-123")
    clock = FakeClock()
    out = run_once(cfg, now=NOW, transport=httpx.MockTransport(handler), sleep=clock.sleep, monotonic=clock,
                   deadline_sec=10**6, sources=["youtube"])
    assert out.exit_code == 0
    assert out.results["youtube"].detail == "RSS 77/77 channels · API 1 calls"
    assert len(seen) == 1
    req = seen[0]
    assert req.url.params["part"] == "statistics"
    # FIXTURE0003 had no RSS statistics, so it is the only candidate; Shorts are never requested.
    assert req.url.params["id"] == "FIXTURE0003"
    import sqlite3

    conn = sqlite3.connect(cfg.db_path)
    rows = conn.execute("SELECT video_id, views, source FROM video_snapshots WHERE source='api'").fetchall()
    errors = "".join(r[0] or "" for r in conn.execute("SELECT error FROM source_runs"))
    conn.close()
    assert rows == [("FIXTURE0003", 777, "api")]
    assert "SECRET-KEY-123" not in errors


def test_no_key_means_no_api_calls(config):
    net = FakeNet()
    clock = FakeClock()
    out = run_once(config, now=NOW, transport=net.transport, sleep=clock.sleep, monotonic=clock,
                   deadline_sec=10**6, sources=["youtube"])
    assert out.results["youtube"].detail == "RSS 77/77 channels"
    assert not any("googleapis" in c for c in net.calls)
