"""YouTube Data API path (spec §3.3): discovery, exact stats, Shorts rule, quota guard, secret hygiene."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import httpx
import pytest

from seoulpulse.config import load_config
from seoulpulse.fetchers import youtube_api as ya
from seoulpulse.fetchers.base import ApiSnapshot, ApiUsage, RunContext, StoreView, VideoObs
from seoulpulse.pipeline import run_once
from seoulpulse.seed import load_seed
from seoulpulse.store import Store

from .conftest import DATA_DIR, FakeClock, utc
from .fakenet import FakeNet

NOW = utc(2026, 10, 14, 9, 0, 0)
PT_DAY = date(2026, 10, 14)
KEY = "SECRET-KEY-123"
HYBE = "UC3IZKseVpdzPSBaWxBxundA"


def upload(vid, title, published, *, private=False):
    item = {"snippet": {"title": title, "resourceId": {"videoId": vid},
                        "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}}},
            "contentDetails": {"videoId": vid}}
    if not private:
        item["contentDetails"]["videoPublishedAt"] = published
    return item


HYBE_UPLOADS = [
    upload("FIXTURE0001", "LE SSERAFIM (르세라핌) 'Test Pattern' Official MV", "2026-10-13T09:00:02Z"),  # in RSS
    upload("APIDISC0001", "aespa 에스파 'Fixture Drive' MV", "2026-10-06T09:00:00Z"),          # older than RSS
    upload("APIDISC0002", "IVE 아이브 'Fixture' Dance Challenge", "2026-10-11T09:00:00Z"),     # 45 s → Short
    upload("APIDISC0003", "ILLIT (아일릿) 'Fixture' Official Teaser", "2026-10-12T09:00:00Z"),  # 40 s teaser
    upload("APIDISC0004", "NewJeans 'Fixture' #shorts", "2026-10-12T10:00:00Z"),             # skipped, no stats
    upload("APIDISC0005", "Private video", "", private=True),                                # skipped
    upload("APIDISC0006", "Office tour vlog", "2026-10-12T11:00:00Z"),                        # unattributed
    upload("APIDISC0007", "aespa 에스파 'Old Song' MV", "2026-08-01T09:00:00Z"),              # >30 d
    upload("APIDISC0008", "KISS OF LIFE 'Fixture' MV", "2026-10-14T08:00:00Z"),              # premiere, P0D
]
STATS = {
    "FIXTURE0001": ("1300000", "PT3M30S"), "FIXTURE0003": ("5000", "PT0M35S"), "FIXTURE0004": ("210000", "PT4M"),
    "APIDISC0001": ("2000000", "PT3M20S"), "APIDISC0002": ("90000", "PT45S"), "APIDISC0003": ("300000", "PT40S"),
    "APIDISC0008": ("0", "P0D"),
}


class FakeYouTubeApi:
    """googleapis.com YouTube Data API v3 double. Records every request."""

    def __init__(self, error=None):
        self.requests: list[httpx.Request] = []
        self.error = error          # (status, body) returned for every API call when set

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.error is not None:
            return httpx.Response(self.error[0], content=json.dumps(self.error[1]).encode())
        if req.url.path == "/youtube/v3/playlistItems":
            items = HYBE_UPLOADS if req.url.params["playlistId"] == "UU" + HYBE[2:] else []
            return httpx.Response(200, content=json.dumps({"items": items}).encode())
        if req.url.path == "/youtube/v3/videos":
            ids = req.url.params["id"].split(",")
            items = [{"id": i, "statistics": {"viewCount": STATS[i][0]}, "contentDetails": {"duration": STATS[i][1]}}
                     for i in ids if i in STATS]
            return httpx.Response(200, content=json.dumps({"items": items}).encode())
        return httpx.Response(404)

    def calls(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/" + kind)]


def run(cfg, api: FakeYouTubeApi | None = None, now=NOW, **kw):
    net = FakeNet()
    base = net.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "www.googleapis.com":
            assert api is not None, "no API call expected"
            return api(req)
        return base(req)

    clock = FakeClock()
    out = run_once(cfg, now=now, transport=httpx.MockTransport(handler), sleep=clock.sleep, monotonic=clock,
                   rand=lambda: 0.0, deadline_sec=10**6, sources=kw.pop("sources", ["youtube"]), **kw)
    return out, net


def db_rows(cfg, sql, *args):
    conn = sqlite3.connect(cfg.db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------- pure parts


@pytest.mark.parametrize(("raw", "secs"), [
    ("PT3M12S", 192), ("PT59S", 59), ("PT1H2M", 3720), ("P1DT2H", 93600), ("P0D", 0), ("PT0S", 0),
    ("PT1M0.5S", 60), ("", None), (None, None), ("junk", None), ("P", None),
])
def test_parse_duration(raw, secs):
    assert ya.parse_duration(raw) == secs


@pytest.mark.parametrize(("title", "secs", "short"), [
    ("IVE 아이브 'Fixture' Dance Challenge", 45, True),        # ≤60 s, not a teaser
    ("ILLIT (아일릿) 'Fixture' Official Teaser", 40, False),    # horizontal teaser clips are not Shorts
    ("aespa 'X' MV", 200, False),
    ("aespa 'X' MV #Shorts", 150, True),                       # #shorts and ≤180 s
    ("aespa 'X' #shorts", 400, False),                         # too long to be a Short
    ("aespa 'X' MV", 0, None),                                 # premiere / live: decide later
    ("aespa 'X' MV", None, None),
])
def test_is_api_short(title, secs, short):
    assert ya.is_api_short(title, secs) is short


def test_parsers_and_error_reasons():
    items = ya.parse_playlist_items(json.dumps({"items": HYBE_UPLOADS}).encode(), HYBE)
    ids = [u.video_id for u in items]
    assert "APIDISC0005" not in ids and ids[0] == "FIXTURE0001" and len(ids) == 8
    assert items[1].thumbnail_url == "https://i.ytimg.com/vi/APIDISC0001/hqdefault.jpg"
    stats = ya.parse_video_items(json.dumps({"items": [
        {"id": "A0000000001", "statistics": {"viewCount": "12"}, "contentDetails": {"duration": "PT1M"}},
        {"id": "A0000000002", "statistics": {}, "contentDetails": {}},
    ]}).encode())
    assert stats["A0000000001"] == ya.VideoStat(12, 60) and stats["A0000000002"] == ya.VideoStat(None, None)
    quota = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded", "domain": "youtube.quota"}]}}
    bad_key = {"error": {"code": 400, "errors": [{"reason": "badRequest"}], "status": "INVALID_ARGUMENT",
                         "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}}
    assert ya.api_error_reason(json.dumps(quota).encode()) == "quotaExceeded"
    assert ya.api_error_reason(json.dumps(bad_key).encode()) == "API_KEY_INVALID"
    assert ya.api_error_reason(b"<html>") is None
    assert ya.uploads_playlist_id(HYBE) == "UU3IZKseVpdzPSBaWxBxundA"


def test_discovery_rotation_covers_every_channel_once_per_6h(config):
    seed = load_seed(DATA_DIR)
    channels = sorted(c.channel_id for c in seed.resolved_channels())
    view = StoreView(channels_with_videos=frozenset(channels))
    seen: list[str] = []
    for slot in range(ya.DISCOVERY_SLOTS):
        ctx = RunContext(now=NOW + timedelta(minutes=30 * slot), http=None, seed=seed, config=config,  # type: ignore[arg-type]
                         store=view, log=logging.getLogger("t"))
        picked = ya.discovery_channels(ctx, set())
        assert len(picked) <= 12, "rotation spreads ~77 channels over 12 slots"
        seen += picked
    assert sorted(seen) == channels                      # each exactly once per 12 slots
    ctx = RunContext(now=NOW, http=None, seed=seed, config=config, store=StoreView(),  # type: ignore[arg-type]
                     log=logging.getLogger("t"))
    assert len(ya.discovery_channels(ctx, set())) == len(channels)     # cold start: everything unseen
    ctx = RunContext(now=NOW, http=None, seed=seed, config=config, store=view, log=logging.getLogger("t"))
    assert ya.discovery_channels(ctx, {channels[5]})[0] == channels[5]   # an RSS failure goes first


# ---------------------------------------------------------------------------- pipeline


def test_no_key_means_no_api_calls_and_unchanged_detail(config):
    out, net = run(config)
    assert out.results["youtube"].detail == "RSS 77/77 channels"
    assert not any("googleapis" in c for c in net.calls)
    assert db_rows(config, "SELECT * FROM api_usage") == []


def test_key_path_discovers_tracks_and_replaces_rss_counts(config, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi()
    out, _ = run(cfg, api)
    assert out.exit_code == 0
    # Cold start: every resolved channel's uploads playlist once (77 units) + one videos.list call.
    assert len(api.calls("playlistItems")) == 77 and len(api.calls("videos")) == 1
    assert out.results["youtube"].detail == "RSS 77/77 channels · API 78 units (78/9000 today)"
    vreq = api.calls("videos")[0]
    assert vreq.url.params["part"] == "statistics,contentDetails"
    # priority: tracked-without-RSS-views, then discovered, then RSS-covered upgrades; Shorts never requested
    assert vreq.url.params["id"].split(",") == [
        "FIXTURE0003", "APIDISC0008", "APIDISC0003", "APIDISC0002", "APIDISC0001", "FIXTURE0001", "FIXTURE0004"]

    snaps = dict(((v, s), n) for v, n, s in db_rows(cfg, "SELECT video_id, views, source FROM video_snapshots"))
    assert snaps[("FIXTURE0001", "api")] == 1_300_000 and ("FIXTURE0001", "rss") not in snaps   # exact count wins
    assert snaps[("FIXTURE0004", "api")] == 210_000 and ("FIXTURE0004", "rss") not in snaps
    assert snaps[("FIXTURE0003", "api")] == 5_000
    assert snaps[("FIXTURE0002", "rss")] == 45_000                                  # RSS Short: untouched
    assert snaps[("APIDISC0001", "api")] == 2_000_000 and snaps[("APIDISC0003", "api")] == 300_000
    videos = {v: (k, a, u) for v, k, a, u in db_rows(cfg, "SELECT video_id, kind, artist_id, url FROM videos")}
    assert videos["APIDISC0001"] == ("mv", "aespa", "https://www.youtube.com/watch?v=APIDISC0001")
    assert videos["APIDISC0002"] == ("short", "ive", "https://www.youtube.com/shorts/APIDISC0002")
    assert videos["APIDISC0003"][0] == "teaser"
    for skipped in ("APIDISC0004", "APIDISC0005", "APIDISC0006", "APIDISC0007", "APIDISC0008"):
        assert skipped not in videos
    assert db_rows(cfg, "SELECT api, day, units FROM api_usage") == [("youtube", "2026-10-14", 78)]
    # aespa now scores from the API-discovered MV, which RSS never listed.
    aespa = next(it for it in out.payload["items"] if it["id"] == "aespa")
    assert aespa["top_video"]["video_id"] == "APIDISC0001"

    # Secret hygiene: header only, never in a URL, the DB, trends.json or any log line.
    for r in api.requests:
        assert r.headers["x-goog-api-key"] == KEY
        assert KEY not in str(r.url) and "key=" not in str(r.url)
    conn = sqlite3.connect(cfg.db_path)
    dump = "\n".join(conn.iterdump())
    conn.close()
    assert KEY not in dump
    assert KEY not in cfg.trends_path.read_text(encoding="utf-8")
    assert KEY not in caplog.text
    assert KEY not in repr(cfg) and "youtube_api_key=<set>" in repr(cfg)


def test_second_run_tracks_videos_that_left_rss(config):
    cfg = replace(config, youtube_api_key=KEY)
    run(cfg, FakeYouTubeApi())
    api = FakeYouTubeApi()
    run(cfg, api, now=NOW + timedelta(minutes=30))
    ids = [i for r in api.calls("videos") for i in r.url.params["id"].split(",")]
    # The discovered MV and teaser are not in RSS but stay tracked through videos.list.
    assert "APIDISC0001" in ids and "APIDISC0003" in ids and "APIDISC0002" not in ids    # Shorts are not
    n = db_rows(cfg, "SELECT COUNT(*) FROM video_snapshots WHERE video_id='APIDISC0001' AND source='api'")[0][0]
    assert n == 2


def test_daily_budget_is_a_hard_ceiling(config):
    cfg = replace(config, youtube_api_key=KEY, youtube_daily_units=150)
    total_requests = 0
    for i in range(40):                                     # 40 runs within one Pacific-time day
        api = FakeYouTubeApi()
        out, _ = run(cfg, api, now=NOW + timedelta(minutes=15 * i))
        total_requests += len(api.requests)
        used = db_rows(cfg, "SELECT units FROM api_usage WHERE api='youtube' AND day='2026-10-14'")[0][0]
        assert used == total_requests <= 150                 # ledger == real calls, never above the budget
        if i == 0:
            assert len(api.requests) == 76                   # discovery capped at half (75) + 1 stats call
            assert "discovery cap" in out.results["youtube"].detail
        if not api.requests:
            assert "daily budget used" in out.results["youtube"].detail
            break
    assert used == 150 and not api.requests
    # A new Pacific-time day starts a fresh budget (2026-10-15 08:00 UTC = 01:00 PDT).
    api = FakeYouTubeApi()
    run(cfg, api, now=utc(2026, 10, 15, 8, 0, 0))
    assert api.requests


def test_ledger_near_limit_and_run_cap(config, monkeypatch):
    cfg = replace(config, youtube_api_key=KEY)
    st = Store(cfg.db_path)
    with st.tx():
        st.add_api_usage(ApiUsage("youtube", PT_DAY, 9000 - 3))
    st.close()
    api = FakeYouTubeApi()
    out, _ = run(cfg, api)
    assert len(api.requests) <= 3
    assert db_rows(cfg, "SELECT units FROM api_usage WHERE api='youtube'")[0][0] <= 9000
    assert "discovery cap" in out.results["youtube"].detail

    monkeypatch.setattr(ya, "RUN_CAP", 5)
    cfg2 = replace(cfg, db_path=cfg.db_path.with_name("other.db"))
    api = FakeYouTubeApi()
    run(cfg2, api)
    assert len(api.calls("playlistItems")) == 2         # discovery may use half of the run cap
    assert len(api.calls("videos")) == 1 and len(api.requests) <= 5


def test_quota_exhausted_stops_and_books_the_day(config):
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi(error=(403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}))
    out, _ = run(cfg, api)
    assert len(api.requests) == 1
    detail = out.results["youtube"].detail
    assert "quota exhausted" in detail and detail.startswith("RSS 77/77 channels · API 1 units (9000/9000 today)")
    assert out.results["youtube"].status == "ok"                                   # RSS still carried the run
    # RSS values stand (the fixture feed is served for every channel, hence several observations)
    snaps = db_rows(cfg, "SELECT DISTINCT views, source FROM video_snapshots WHERE video_id='FIXTURE0001'")
    assert snaps == [(1234567, "rss")]
    api2 = FakeYouTubeApi()
    run(cfg, api2, now=NOW + timedelta(minutes=30))
    assert api2.requests == []


def test_rejected_key_stops_after_one_call(config):
    cfg = replace(config, youtube_api_key=KEY)
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "errors": [{"reason": "badRequest"}],
                      "details": [{"reason": "API_KEY_INVALID"}]}}
    out, _ = run(cfg, FakeYouTubeApi(error=(400, body)))
    assert "key rejected" in out.results["youtube"].detail
    assert any("API_KEY_INVALID" in e for e in out.results["youtube"].errors)
    assert db_rows(cfg, "SELECT units FROM api_usage")[0][0] == 1


def test_missing_key_answer_stops_after_one_call(config):
    cfg = replace(config, youtube_api_key=KEY)
    body = {"error": {"code": 403, "message": "The request is missing a valid API key.",
                      "errors": [{"reason": "forbidden", "domain": "global"}], "status": "PERMISSION_DENIED"}}
    api = FakeYouTubeApi(error=(403, body))
    out, _ = run(cfg, api)
    assert len(api.requests) == 1 and "key rejected" in out.results["youtube"].detail


def test_transport_errors_never_carry_the_key(config):
    cfg = replace(config, youtube_api_key=KEY)
    net = FakeNet()

    def handler(req):
        if req.url.host == "www.googleapis.com":
            raise httpx.ConnectError(f"boom {req.url} {dict(req.headers)}", request=req)
        return net.handler(req)

    clock = FakeClock()
    out = run_once(cfg, now=NOW, transport=httpx.MockTransport(handler), sleep=clock.sleep, monotonic=clock,
                   rand=lambda: 0.0, deadline_sec=10**6, sources=["youtube"])
    errs = "\n".join(out.results["youtube"].errors)
    assert "boom" in errs                                    # the error is reported...
    stored = "".join(r[0] or "" for r in db_rows(cfg, "SELECT error FROM source_runs"))
    assert KEY not in errs and KEY not in stored              # ...but the key is not (it was only a header)


def test_retention_drops_stale_api_only_data_after_30_days(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    old = NOW - timedelta(days=31)
    with st.tx():
        st.upsert_video(VideoObs("RSSVIDEO001", HYBE, "t", "https://www.youtube.com/watch?v=RSSVIDEO001",
                                 old, 100, old), "mv", "aespa")
        st.upsert_video(VideoObs("APIONLY0001", HYBE, "t", "https://www.youtube.com/watch?v=APIONLY0001",
                                 old, None, old), "mv", "aespa")
        st.insert_api_snapshot(ApiSnapshot("APIONLY0001", old, 500))
        st.insert_api_snapshot(ApiSnapshot("RSSVIDEO001", old, 600))
        st.add_api_usage(ApiUsage("youtube", (NOW - timedelta(days=41)).date(), 5))
    st.apply_retention(NOW)
    ids = {r[0] for r in st.conn.execute("SELECT video_id FROM videos")}
    snaps = {(r[0], r[1]) for r in st.conn.execute("SELECT video_id, source FROM video_snapshots")}
    assert ids == {"RSSVIDEO001"}                             # RSS-backed rows keep the 35-day rule
    assert snaps == {("RSSVIDEO001", "rss")}                  # API data never outlives 30 days
    assert st.api_usage() == {}
    st.close()


def test_load_config_reads_budget_and_clamps():
    assert load_config({}, warn=False).youtube_daily_units == 9000
    assert load_config({"SP_YOUTUBE_DAILY_UNITS": "2500"}, warn=False).youtube_daily_units == 2500
    assert load_config({"SP_YOUTUBE_DAILY_UNITS": "99999"}, warn=False).youtube_daily_units == 10000
