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
from seoulpulse.fetchers.base import ApiSnapshot, ApiUsage, RunContext, StoreView, UploadsState, VideoObs
from seoulpulse.pipeline import run_once
from seoulpulse.seed import load_seed
from seoulpulse.store import Store

from .conftest import DATA_DIR, FakeClock, utc
from .fakenet import FakeNet, ok_feed

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

    def __init__(self, error=None, *, missing=(), resolve=None, real_uploads=(), video_counts=None, search=None,
                 search_error=None):
        self.requests: list[httpx.Request] = []
        self.error = error          # (status, body) returned for every API call when set
        self.missing = set(missing)         # playlist ids answering 404 playlistNotFound
        self.resolve = resolve or {}        # channels.list: channel id -> its real uploads playlist id
        self.real_uploads = set(real_uploads)   # extra playlist ids that serve HYBE's uploads
        self.video_counts = video_counts or {}  # channels.list: channel id -> statistics.videoCount
        self.search = search or {}          # search.list: channel id -> result items
        self.search_error = search_error    # (status, body) for search.list only

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.error is not None:
            return httpx.Response(self.error[0], content=json.dumps(self.error[1]).encode())
        if req.url.path == "/youtube/v3/playlistItems":
            pid = req.url.params["playlistId"]
            if pid in self.missing:
                body = {"error": {"code": 404, "message": "The playlist identified with the request's playlistId "
                                  "parameter cannot be found.", "errors": [{"reason": "playlistNotFound"}]}}
                return httpx.Response(404, content=json.dumps(body).encode())
            items = HYBE_UPLOADS if pid in ({"UU" + HYBE[2:]} | self.real_uploads) else []
            return httpx.Response(200, content=json.dumps({"items": items}).encode())
        if req.url.path == "/youtube/v3/channels":
            items = []
            for c in req.url.params["id"].split(","):
                if c not in self.resolve and c not in self.video_counts:
                    continue                                  # unknown (deleted/terminated) channel: no item
                item = {"id": c, "contentDetails": {"relatedPlaylists": {}}}
                if c in self.resolve:
                    item["contentDetails"]["relatedPlaylists"]["uploads"] = self.resolve[c]
                if c in self.video_counts:
                    item["statistics"] = {"videoCount": str(self.video_counts[c]), "hiddenSubscriberCount": False}
                items.append(item)
            return httpx.Response(200, content=json.dumps({"items": items}).encode())
        if req.url.path == "/youtube/v3/search":
            if self.search_error is not None:
                return httpx.Response(self.search_error[0], content=json.dumps(self.search_error[1]).encode())
            items = self.search.get(req.url.params["channelId"], [])
            return httpx.Response(200, content=json.dumps({"kind": "youtube#searchListResponse",
                                                           "items": items}).encode())
        if req.url.path == "/youtube/v3/videos":
            ids = req.url.params["id"].split(",")
            items = [{"id": i, "statistics": {"viewCount": STATS[i][0]}, "contentDetails": {"duration": STATS[i][1]}}
                     for i in ids if i in STATS]
            return httpx.Response(200, content=json.dumps({"items": items}).encode())
        return httpx.Response(404)

    def calls(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/" + kind)]

    def units(self) -> int:
        """What Google bills for the recorded requests: search.list 100, everything else 1."""
        return sum(ya.SEARCH_COST if r.url.path.endswith("/search") else 1 for r in self.requests)


def rss_down(_: httpx.Request) -> httpx.Response:
    """YouTube's feed server during the 2026-10-14 outage: 404 for every channel."""
    return httpx.Response(404, content=b"Not Found")


def run(cfg, api: FakeYouTubeApi | None = None, now=NOW, *, rss=None, **kw):
    net = FakeNet(youtube=rss) if rss is not None else FakeNet()
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
    chans = ya.parse_channel_uploads(json.dumps({"items": [
        {"id": HYBE, "contentDetails": {"relatedPlaylists": {"uploads": "UUreal"}}},
        {"id": "UCnoUploads", "contentDetails": {"relatedPlaylists": {}}}, {"contentDetails": {}}]}).encode())
    assert chans == {HYBE: "UUreal"}


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
    assert db_rows(config, "SELECT * FROM yt_uploads") == []


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


# ---------------------------------------------------------------------------- uploads playlist resolution

DERIVED = "UU" + HYBE[2:]
REAL = "UUREALHYBEPLAYLIST0000"


def hybe_slot_time(after):
    """The first 30-minute step after `after` whose rotation slot contains HYBE's channel."""
    t = after
    while ya.current_slot(t) != ya.discovery_slot(HYBE):
        t += timedelta(minutes=30)
    return t


def warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_derived_playlist_404_resolves_via_channels_list_and_is_cached(config, caplog):
    caplog.set_level(logging.INFO)
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi(missing={DERIVED}, resolve={HYBE: REAL}, real_uploads={REAL})
    out, _ = run(cfg, api)
    res = out.results["youtube"]
    # 77 derived playlists (one 404) + channels.list + the resolved playlist + one videos.list
    assert [len(api.calls(k)) for k in ("playlistItems", "channels", "videos")] == [78, 1, 1]
    assert res.status == "ok" and res.errors == [] and "RSS-only" not in res.detail
    assert res.detail == "RSS 77/77 channels · API 80 units (80/9000 today)"
    assert db_rows(cfg, "SELECT units FROM api_usage") == [(80,)] and len(api.requests) == 80   # every call booked
    creq = api.calls("channels")[0]
    assert creq.url.params["part"] == "contentDetails,statistics" and creq.url.params["id"] == HYBE
    assert creq.headers["x-goog-api-key"] == KEY and KEY not in str(creq.url)
    assert [r.url.params["playlistId"] for r in api.calls("playlistItems")][-1] == REAL
    assert db_rows(cfg, "SELECT channel_id, playlist_id, unavailable_until, method FROM yt_uploads") == [
        (HYBE, REAL, None, "resolved")]
    assert db_rows(cfg, "SELECT video_id FROM videos WHERE video_id='APIDISC0001'")      # discovered via REAL
    assert not warnings(caplog) and not any("playlistNotFound" in r.getMessage() for r in caplog.records)

    # Next time HYBE's slot comes round: the stored id is used directly, no 404 and no channels.list.
    api2 = FakeYouTubeApi(missing={DERIVED}, resolve={HYBE: REAL}, real_uploads={REAL})
    out2, _ = run(cfg, api2, now=hybe_slot_time(NOW + timedelta(minutes=30)))
    assert api2.calls("channels") == []
    ids = [r.url.params["playlistId"] for r in api2.calls("playlistItems")]
    assert REAL in ids and DERIVED not in ids
    assert out2.results["youtube"].errors == []


def test_other_playlist_errors_still_count_as_errors(config):
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi(error=(404, {"error": {"code": 404, "errors": [{"reason": "channelNotFound"}]}}))
    out, _ = run(cfg, api)
    assert any("HTTP 404 channelNotFound" in e for e in out.results["youtube"].errors)
    assert db_rows(cfg, "SELECT * FROM yt_uploads") == []


def test_migration_adds_uploads_table_to_an_existing_db(tmp_path):
    path = tmp_path / "old.sqlite"
    st = Store(path)
    with st.tx():
        st.upsert_video(VideoObs("KEEPVIDEO01", HYBE, "t", "https://www.youtube.com/watch?v=KEEPVIDEO01",
                                 NOW, 100, NOW), "mv", "aespa")
        st.conn.execute("DROP TABLE yt_uploads")                      # a DB written before the table existed
    st.close()
    st = Store(path)                                                  # migrate() on open, like the Actions cache copy
    assert st.uploads_states() == {}
    with st.tx():
        st.upsert_uploads_state(UploadsState(HYBE, REAL, None, NOW))
        st.upsert_uploads_state(UploadsState("UCother", None, NOW + timedelta(days=7), NOW))
    st.close()
    st = Store(path)                                                  # idempotent, data survives
    states = st.uploads_states()
    assert states[HYBE] == UploadsState(HYBE, REAL, None, NOW)
    assert states["UCother"].unavailable_until == NOW + timedelta(days=7)
    assert st.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
    assert st.view(NOW).uploads_state == states
    assert st.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    st.close()


# ---------------------------------------------------------------------------- empty, UULF and API-blind channels
# Live 2026-10-15: six channels answered playlistNotFound for UU… and channels.list returned the same id. They were
# squatter channels with no uploads at all (the seed handles were wrong), so neither UULF nor search.list could
# find anything there; channels.list's videoCount now tells "empty" apart from a genuinely API-blind channel.

ULF = "UULF" + HYBE[2:]


def search_item(vid, title, published, *, channel=HYBE, live="none"):
    return {"kind": "youtube#searchResult", "id": {"kind": "youtube#video", "videoId": vid},
            "snippet": {"publishedAt": published, "channelId": channel, "title": title, "liveBroadcastContent": live,
                        "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}}}}


HYBE_SEARCH = [
    search_item("SEARCH00001", "aespa 에스파 &#39;Search Light&#39; MV", "2026-10-13T12:00:00Z"),   # escaped title
    search_item("SEARCH00002", "IVE 아이브 &#39;Found&#39; Dance Challenge", "2026-10-13T13:00:00Z"),  # 45 s → Short
    search_item("SEARCH00003", "aespa 'Neon' Comeback Live", "2026-10-14T10:00:00Z", live="upcoming"),  # skipped
    search_item("SEARCH00004", "NewJeans 'Other' MV", "2026-10-12T09:00:00Z", channel="UCx" + "0" * 21),  # not HYBE
    search_item("SEARCH00005", "Office tour vlog", "2026-10-12T11:00:00Z"),                           # unattributed
]
STATS.update({"SEARCH00001": ("750000", "PT3M10S"), "SEARCH00002": ("80000", "PT45S")})


def blind_api(**kw):
    """HYBE LABELS with public videos but neither UU… nor UULF… readable: an API-blind channel."""
    return FakeYouTubeApi(missing={DERIVED, ULF}, resolve={HYBE: DERIVED}, video_counts={HYBE: 3600},
                          search={HYBE: HYBE_SEARCH}, **kw)


def uploads_row(cfg):
    return db_rows(cfg, "SELECT channel_id, playlist_id, unavailable_until, method, searched_at FROM yt_uploads")


def test_search_and_channel_parsers():
    found = ya.parse_search_items(json.dumps({"items": HYBE_SEARCH + [
        {"id": {"kind": "youtube#channel", "channelId": HYBE}, "snippet": {"title": "x"}},   # not a video
        {"id": "SEARCH00009", "snippet": {}}]}).encode(), HYBE)
    assert [u.video_id for u in found] == ["SEARCH00001", "SEARCH00002", "SEARCH00005"]
    assert found[0].title == "aespa 에스파 'Search Light' MV"                 # HTML entities decoded
    assert found[0].published_at == utc(2026, 10, 13, 12, 0, 0)
    assert found[0].thumbnail_url == "https://i.ytimg.com/vi/SEARCH00001/hqdefault.jpg"
    info = ya.parse_channels(json.dumps({"items": [
        {"id": HYBE, "contentDetails": {"relatedPlaylists": {"uploads": DERIVED}}, "statistics": {"videoCount": "0"}},
        {"id": "UCnoStats", "contentDetails": {}}, {"statistics": {"videoCount": "4"}}]}).encode())
    assert info == {HYBE: ya.ChannelInfo(DERIVED, 0), "UCnoStats": ya.ChannelInfo(None, None)}
    assert ya.long_form_playlist_id(HYBE) == ULF
    params = ya.search_params(HYBE, NOW)
    assert params == {"part": "snippet", "channelId": HYBE, "type": "video", "order": "date",
                      "publishedAfter": "2026-09-14T09:00:00Z", "maxResults": "50"}
    # 09:00 UTC on 2026-10-14 is 02:00 PDT: 22 h of the quota day left, at one run per 20 min.
    assert ya.runs_left_today(NOW) == 66 and ya.runs_left_today(utc(2026, 10, 15, 6, 50, 0)) == 0


@pytest.mark.parametrize("api_kw", [
    {"video_counts": {HYBE: 0}, "resolve": {HYBE: DERIVED}},   # live case: the channel has no public videos
    {},                                                        # channels.list does not know the channel at all
])
def test_empty_channel_is_flagged_and_never_searched(config, caplog, api_kw):
    caplog.set_level(logging.INFO)
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi(missing={DERIVED}, **api_kw)
    out, _ = run(cfg, api)
    res = out.results["youtube"]
    # 77 playlists (one 404) + channels.list + videos.list; no UULF, no search: there is nothing to find
    assert [len(api.calls(k)) for k in ("playlistItems", "channels", "search", "videos")] == [77, 1, 0, 1]
    assert res.status == "ok" and res.errors == []
    assert res.detail == "RSS 77/77 channels · API 79 units (79/9000 today) · 1 channel empty"
    assert uploads_row(cfg) == [(HYBE, None, "2026-10-21T09:00:00Z", "empty", None)]
    warned = warnings(caplog)
    assert len(warned) == 1 and "has no public videos" in warned[0].getMessage() and KEY not in caplog.text

    # During an RSS outage inside the 7 days: no playlist, no channels.list and no search for it.
    api2 = FakeYouTubeApi(missing={DERIVED}, **api_kw)
    out2, _ = run(cfg, api2, now=NOW + timedelta(minutes=30), rss=rss_down)
    assert api2.calls("search") == [] and api2.calls("channels") == []
    assert all(r.url.params["playlistId"] != DERIVED for r in api2.calls("playlistItems"))
    assert out2.results["youtube"].units_ok == 76 and "1 channel empty" in out2.results["youtube"].detail

    # After 7 days it is probed again (once, on its rotation slot).
    api3 = FakeYouTubeApi(missing={DERIVED}, **api_kw)
    run(cfg, api3, now=hybe_slot_time(NOW + timedelta(days=8)))
    assert DERIVED in [r.url.params["playlistId"] for r in api3.calls("playlistItems")]
    assert len(api3.calls("channels")) == 1


def test_long_form_playlist_is_the_first_fallback_and_is_cached(config):
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi(missing={DERIVED}, resolve={HYBE: DERIVED}, video_counts={HYBE: 3600}, real_uploads={ULF})
    out, _ = run(cfg, api)
    res = out.results["youtube"]
    assert [len(api.calls(k)) for k in ("playlistItems", "channels", "search", "videos")] == [78, 1, 0, 1]
    assert [r.url.params["playlistId"] for r in api.calls("playlistItems")][-1] == ULF
    assert res.detail == "RSS 77/77 channels · API 80 units (80/9000 today)" and res.errors == []
    assert uploads_row(cfg) == [(HYBE, ULF, None, "uulf", None)]
    assert db_rows(cfg, "SELECT kind, artist_id FROM videos WHERE video_id='APIDISC0001'") == [("mv", "aespa")]

    api2 = FakeYouTubeApi(missing={DERIVED}, resolve={HYBE: DERIVED}, video_counts={HYBE: 3600}, real_uploads={ULF})
    run(cfg, api2, now=hybe_slot_time(NOW + timedelta(minutes=30)))
    ids = [r.url.params["playlistId"] for r in api2.calls("playlistItems")]
    assert ULF in ids and DERIVED not in ids and api2.calls("channels") == []


def test_api_blind_channel_is_searched_while_its_rss_fails(config):
    cfg = replace(config, youtube_api_key=KEY)
    # Run 1, RSS fine: the probe finds no readable playlist; RSS still lists the channel, so no search yet.
    api = blind_api()
    out, _ = run(cfg, api)
    assert [len(api.calls(k)) for k in ("playlistItems", "channels", "search", "videos")] == [78, 1, 0, 1]
    assert out.results["youtube"].detail == "RSS 77/77 channels · API 80 units (80/9000 today) · 1 channel via search"
    assert uploads_row(cfg) == [(HYBE, None, "2026-10-21T09:00:00Z", "search", None)]

    # Run 2, global RSS outage: every other channel is read through its playlist this run; HYBE through search.
    t2 = NOW + timedelta(minutes=30)
    api2 = blind_api()
    out2, _ = run(cfg, api2, now=t2, rss=rss_down)
    res = out2.results["youtube"]
    assert len(api2.calls("playlistItems")) == 76 and len(api2.calls("search")) == 1
    sreq = api2.calls("search")[0]
    assert dict(sreq.url.params) == {**ya.search_params(HYBE, t2)}
    assert sreq.headers["x-goog-api-key"] == KEY and KEY not in str(sreq.url)
    assert res.status == "ok" and (res.units_ok, res.units_total) == (77, 77)        # search bridged HYBE too
    units = api2.units()
    assert units == 76 + 100 + len(api2.calls("videos"))
    assert res.detail == f"RSS 0/77 + API 77 channels · {units} units ({80 + units}/9000 today) · 1 channel via search"
    assert db_rows(cfg, "SELECT units FROM api_usage") == [(80 + units,)]               # every unit booked
    # Search results take the same path as playlist uploads: attribution, Shorts rule, exact stats.
    vids = [i for r in api2.calls("videos") for i in r.url.params["id"].split(",")]
    assert "SEARCH00001" in vids and "SEARCH00002" in vids
    assert not {"SEARCH00003", "SEARCH00004", "SEARCH00005"} & set(vids)
    rows = {v: (k, a, u, ti) for v, k, a, u, ti in db_rows(cfg, "SELECT video_id, kind, artist_id, url, title FROM videos"
                                                          " WHERE video_id LIKE 'SEARCH%'")}
    assert rows == {
        "SEARCH00001": ("mv", "aespa", "https://www.youtube.com/watch?v=SEARCH00001", "aespa 에스파 'Search Light' MV"),
        "SEARCH00002": ("short", "ive", "https://www.youtube.com/shorts/SEARCH00002", "IVE 아이브 'Found' Dance Challenge"),
    }
    assert db_rows(cfg, "SELECT views, source FROM video_snapshots WHERE video_id='SEARCH00001'") == [(750000, "api")]
    assert uploads_row(cfg)[0][3:] == ("search", "2026-10-14T09:30:00Z")

    # Run 3, 30 min later, still no RSS: within 6 h the channel is not searched again (and counts as failed).
    api3 = blind_api()
    out3, _ = run(cfg, api3, now=t2 + timedelta(minutes=30), rss=rss_down)
    assert api3.calls("search") == [] and out3.results["youtube"].units_ok == 76
    # 6 h after the last search it is due again.
    api4 = blind_api()
    run(cfg, api4, now=t2 + timedelta(hours=6), rss=rss_down)
    assert len(api4.calls("search")) == 1


def seed_blind_state(cfg, *, used=0, searched_at=None):
    st = Store(cfg.db_path)
    with st.tx():
        st.upsert_uploads_state(UploadsState(HYBE, None, NOW + timedelta(days=6), NOW - timedelta(days=1),
                                             ya.METHOD_SEARCH, searched_at))
        if used:
            st.add_api_usage(ApiUsage("youtube", PT_DAY, used))
    st.close()


def test_search_waits_for_budget_headroom(config):
    # 4,000 units used by 02:00 PDT: a search now would leave too little for the 66 runs still to come (~79 each).
    cfg = replace(config, youtube_api_key=KEY)
    seed_blind_state(cfg, used=4000)
    api = blind_api()
    out, _ = run(cfg, api, rss=rss_down)
    assert api.calls("search") == [] and "search deferred" in out.results["youtube"].detail
    assert uploads_row(cfg)[0][4] is None                       # not stamped: searched as soon as there is room
    # Late in the quota day (23:30 PDT) the same ledger leaves plenty of room.
    cfg2 = replace(cfg, db_path=cfg.db_path.with_name("late.db"))
    seed_blind_state(cfg2, used=4000)
    api2 = blind_api()
    run(cfg2, api2, now=utc(2026, 10, 15, 6, 30, 0), rss=rss_down)
    assert len(api2.calls("search")) == 1


def test_search_units_ride_on_top_of_the_run_cap_and_are_booked_when_it_fails(config, monkeypatch):
    monkeypatch.setattr(ya, "RUN_CAP", 120)                     # discovery cap 60 of the 76 failed channels
    cfg = replace(config, youtube_api_key=KEY)
    seed_blind_state(cfg)
    body = {"error": {"code": 400, "errors": [{"reason": "badRequest"}], "message": "Invalid argument"}}
    api = blind_api(search_error=(400, body))
    out, _ = run(cfg, api, rss=rss_down)
    res = out.results["youtube"]
    assert len(api.calls("playlistItems")) == 60 and len(api.calls("search")) == 1
    assert api.units() == 60 + 100 + len(api.calls("videos")) > ya.RUN_CAP        # regular work ≤ cap, + 1 search
    assert db_rows(cfg, "SELECT units FROM api_usage") == [(api.units(),)]          # the failed search is billed
    assert uploads_row(cfg)[0][4] == "2026-10-14T09:00:00Z"     # stamped: not retried every run
    assert "discovery cap" in res.detail

    # The failure is reported (seen here with only HYBE's feed down, so RSS errors do not fill the error list).
    def hybe_down(req: httpx.Request) -> httpx.Response:
        return rss_down(req) if req.url.params["channel_id"] == HYBE else ok_feed(req)

    cfg2 = replace(cfg, db_path=cfg.db_path.with_name("one.db"))
    seed_blind_state(cfg2)
    api2 = blind_api(search_error=(400, body))
    out2, _ = run(cfg2, api2, rss=hybe_down)
    assert len(api2.calls("search")) == 1
    assert any(e == f"API search {HYBE}: HTTP 400 badRequest" for e in out2.results["youtube"].errors)


def test_rows_from_before_methods_are_probed_again(config):
    cfg = replace(config, youtube_api_key=KEY)
    st = Store(cfg.db_path)
    with st.tx():                                               # what the 2026-10-14 code wrote ("RSS-only")
        st.upsert_uploads_state(UploadsState(HYBE, None, NOW + timedelta(days=5), NOW - timedelta(days=2)))
    st.close()
    api = FakeYouTubeApi(missing={DERIVED}, resolve={HYBE: DERIVED}, video_counts={HYBE: 0})
    run(cfg, api, rss=rss_down)                                 # RSS failure puts HYBE on this run's list
    assert DERIVED in [r.url.params["playlistId"] for r in api.calls("playlistItems")]
    assert uploads_row(cfg)[0][3] == "empty"


def test_capped_outage_discovery_rotates_instead_of_starving_channels(config):
    seed = load_seed(DATA_DIR)
    channels = sorted(c.channel_id for c in seed.resolved_channels())
    view = StoreView(channels_with_videos=frozenset(channels))
    seen: set[str] = set()
    firsts = []
    for k in range(ya.DISCOVERY_SLOTS):
        ctx = RunContext(now=NOW + timedelta(minutes=30 * k), http=None, seed=seed, config=config,  # type: ignore[arg-type]
                         store=view, log=logging.getLogger("t"))
        picked = ya.discovery_channels(ctx, set(channels))[:20]     # a cap that takes only 20 of 77 failures
        firsts.append(picked[0])
        seen |= set(picked)
    assert seen == set(channels)                                    # nobody waits longer than 12 runs
    assert len(set(firsts)) > 1


def test_global_rss_outage_reads_every_channel_this_run_within_the_daily_budget(config):
    cfg = replace(config, youtube_api_key=KEY)
    api = FakeYouTubeApi()
    out, _ = run(cfg, api, rss=rss_down)
    assert len(api.calls("playlistItems")) == 77                    # every failed channel is read this run
    per_run = api.units()
    assert per_run <= ya.RUN_CAP and "cap" not in out.results["youtube"].detail
    # 48 runs a day of this, plus four searches a day for each of six API-blind channels, fits 9,000.
    assert 48 * per_run + 6 * 4 * ya.SEARCH_COST <= cfg.youtube_daily_units


def test_migration_adds_method_columns_to_an_existing_uploads_table(tmp_path):
    path = tmp_path / "old.sqlite"
    st = Store(path)
    with st.tx():
        st.conn.execute("DROP TABLE yt_uploads")
        st.conn.execute("CREATE TABLE yt_uploads (channel_id TEXT PRIMARY KEY, playlist_id TEXT,"
                        " unavailable_until TEXT, checked_at TEXT NOT NULL)")       # the 2026-10-14 layout
        st.conn.execute("INSERT INTO yt_uploads VALUES (?, NULL, '2026-10-21T09:00:00Z', '2026-10-14T09:00:00Z')",
                        (HYBE,))
    st.close()
    st = Store(path)
    assert st.uploads_states() == {HYBE: UploadsState(HYBE, None, NOW + timedelta(days=7), NOW)}
    with st.tx():
        st.upsert_uploads_state(UploadsState(HYBE, None, NOW + timedelta(days=7), NOW, "search", NOW))
    st.close()
    st = Store(path)                                                # idempotent
    assert st.uploads_states()[HYBE].searched_at == NOW
    st.apply_retention(NOW + timedelta(days=8))                     # expired marks go, so the channel is re-probed
    assert st.uploads_states() == {}
    st.close()
