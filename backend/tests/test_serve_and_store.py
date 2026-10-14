from __future__ import annotations

import json
import threading
import urllib.request
from datetime import date, timedelta

import pytest

from seoulpulse.fetchers.base import ApiSnapshot, Headline, VideoObs, WikiDay
from seoulpulse import cli, serve
from seoulpulse.serve import make_server, seed_example
from seoulpulse.store import Store

from .conftest import EXAMPLE_PATH, utc

NOW = utc(2026, 10, 14, 9, 0, 0)


def test_seed_example_copies_only_when_missing(tmp_path):
    pub = tmp_path / "public"
    assert seed_example(pub, EXAMPLE_PATH) is True
    assert (pub / "trends.json").read_bytes() == EXAMPLE_PATH.read_bytes()
    (pub / "trends.json").write_text("{}", encoding="utf-8")
    assert seed_example(pub, EXAMPLE_PATH) is False
    assert (pub / "trends.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.allow_loopback
def test_server_sends_no_cache_and_json(tmp_path):
    pub = tmp_path / "public"
    seed_example(pub, EXAMPLE_PATH)
    server = make_server(pub, port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/trends.json", timeout=5) as r:
            body = r.read()
            assert r.headers["Cache-Control"] == "no-cache"
            assert r.headers["Content-Type"].startswith("application/json")
        assert json.loads(body)["schema_version"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_make_server_binds_requested_host(tmp_path):
    server = make_server(tmp_path / "public", port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"  # default stays loopback-only
    finally:
        server.server_close()
    server = make_server(tmp_path / "public", port=0, host="localhost")
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_cli_host_option_defaults_and_is_plumbed(tmp_path, monkeypatch):
    parser = cli.build_parser()
    assert parser.parse_args(["serve"]).host == "127.0.0.1"
    assert parser.parse_args(["dev"]).host == "127.0.0.1"
    assert parser.parse_args(["serve", "--host", "0.0.0.0"]).host == "0.0.0.0"
    assert parser.parse_args(["dev", "--host", "0.0.0.0"]).host == "0.0.0.0"

    calls = {}
    monkeypatch.setattr(
        serve, "serve_forever", lambda public_dir, port=8787, host="127.0.0.1": calls.update(serve=(port, host))
    )
    monkeypatch.setattr(
        serve, "serve_in_thread", lambda public_dir, port=8787, host="127.0.0.1": calls.update(dev=(port, host))
    )
    import seoulpulse.pipeline as pipeline

    monkeypatch.setattr(pipeline, "run_loop", lambda *a, **k: None)
    monkeypatch.setenv("SP_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SP_PUBLIC_DIR", str(tmp_path / "public"))

    assert cli.main(["serve", "--port", "0", "--host", "0.0.0.0"]) == 0
    assert calls["serve"] == (0, "0.0.0.0")
    assert cli.main(["dev", "--port", "0", "--host", "0.0.0.0"]) == 0
    assert calls["dev"] == (0, "0.0.0.0")
    assert cli.main(["serve", "--port", "0"]) == 0
    assert calls["serve"] == (0, "127.0.0.1")


def test_retention_rules(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    old = NOW - timedelta(days=40)
    with st.tx():
        st.upsert_video(VideoObs("OLDVIDEO001", "UCx", "t", "u", old, 10, old), "mv", "a")
        st.upsert_video(VideoObs("NEWVIDEO001", "UCx", "t", "u", NOW, 10, NOW), "mv", "a")
        st.insert_snapshot("NEWVIDEO001", old, 5, "rss")          # old snapshot of a live video
        st.insert_api_snapshot(ApiSnapshot("NEWVIDEO001", NOW + timedelta(minutes=1), 11))
        st.insert_headline(Headline("h-old", "soompi", "en", "t", "u", "g1", old, old))
        st.insert_headline(Headline("h-new", "soompi", "en", "t", "u", "g2", NOW, NOW))
        st.upsert_wiki_day(WikiDay("en", "X", (NOW - timedelta(days=61)).date(), 1), NOW)
        st.upsert_wiki_day(WikiDay("en", "X", date(2026, 10, 13), 1), NOW)
        for i in range(505):
            st.insert_published(i + 1, NOW - timedelta(minutes=i), "{}")
    st.apply_retention(NOW)
    c = st.conn
    assert [r[0] for r in c.execute("SELECT video_id FROM videos")] == ["NEWVIDEO001"]
    assert c.execute("SELECT COUNT(*) FROM video_snapshots").fetchone()[0] == 2
    assert [r[0] for r in c.execute("SELECT headline_id FROM headlines")] == ["h-new"]
    assert c.execute("SELECT COUNT(*) FROM wiki_daily").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM published").fetchone()[0] == 500
    assert c.execute("SELECT MIN(run_id) FROM published").fetchone()[0] == 1   # newest 500 kept
    st.close()


def test_schema_has_all_tables(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    names = {r[0] for r in st.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "runs", "source_runs", "videos", "video_snapshots", "wiki_daily", "headlines",
            "artist_scores", "published", "http_cache", "why_cache"} <= names
    assert st.get_meta("schema_version") == "1"
    assert st.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    st.close()
