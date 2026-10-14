from __future__ import annotations

import json
from datetime import timedelta

import jsonschema

from seoulpulse.pipeline import run_once
from seoulpulse.scoring import ArtistInfo, Snapshot, Video, is_cold_start, rank_scores, score_all

from .conftest import SCHEMA_PATH, FakeClock, utc
from .fakenet import FakeNet

NOW = utc(2026, 10, 14, 9, 0, 0)


def single_snapshot_video(vid: str, artist: str, kind: str, age_h: float, views: int) -> Video:
    return Video(vid, artist, kind, f"{artist} '{vid}' clip", NOW - timedelta(hours=age_h),
                 (Snapshot(NOW, views),))


def test_cold_start_orders_by_lifetime_intensity():
    artists = [ArtistInfo(a, a.upper()) for a in ("alpha", "bravo", "charlie", "delta")]
    videos = {
        "alpha": [single_snapshot_video("AAAAAAAAAA1", "alpha", "mv", 100, 2_000_000)],
        "bravo": [single_snapshot_video("BBBBBBBBBB1", "bravo", "mv", 300, 9_000_000)],
        "charlie": [single_snapshot_video("CCCCCCCCCC1", "charlie", "perf", 60, 400_000)],
        "delta": [single_snapshot_video("DDDDDDDDDD1", "delta", "other", 200, 900)],   # too small to rank
    }
    scores = score_all(artists, videos, {}, {}, NOW, None)
    ranked = rank_scores(scores)
    by_intensity = sorted((s for s in scores if s.eligible), key=lambda s: -s.yt_raw)
    assert [s.id for s in ranked] == [s.id for s in by_intensity]
    assert "delta" not in [s.id for s in ranked]
    for s in ranked:
        assert s.top.has_pair is False and s.top.estimated is True and s.r_top is None
        assert s.s_accel == 0.0                     # accel is neutral on cold start
    assert is_cold_start(scores)


def test_cold_start_pipeline_run_produces_items(config):
    clock = FakeClock()
    outcome = run_once(config, now=NOW, transport=FakeNet().transport, sleep=clock.sleep, monotonic=clock,
                       rand=lambda: 0.0, deadline_sec=10**6)
    assert outcome.exit_code == 0
    payload = json.loads(config.trends_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert payload["cold_start"] is True
    assert payload["rank_change_basis_at"] is None
    assert payload["items"]
    assert all(it["change"] == "new" and it["prev_rank"] is None for it in payload["items"])
    ids = [it["id"] for it in payload["items"]]
    assert "le-sserafim" in ids
    lsf = next(it for it in payload["items"] if it["id"] == "le-sserafim")
    assert lsf["metrics"]["yt_views_per_hour_estimated"] is True
    assert lsf["metrics"]["yt_accel"] is None
    assert lsf["top_video"]["channel_name"] == "HYBE LABELS"
    assert lsf["links"]["youtube_channel"] == "https://www.youtube.com/channel/UCs-QBT4qkj_YiQw1ZntDO3g"
    assert lsf["links"]["wikipedia_en"] == "https://en.wikipedia.org/wiki/Le_Sserafim"


def test_second_run_has_pairs_and_rank_basis(config):
    """Two runs 30 minutes apart: the second is no longer cold and has prev_rank values."""
    import httpx

    from .conftest import read_fixture

    raw = read_fixture("youtube_hybe_labels.xml").decode()
    bumped = raw.replace('views="1234567"', 'views="1300000"').replace('views="203362"', 'views="210000"')

    clock = FakeClock()
    net1 = FakeNet()
    run_once(config, now=NOW, transport=net1.transport, sleep=clock.sleep, monotonic=clock, deadline_sec=10**6)
    net2 = FakeNet(youtube=lambda req: httpx.Response(200, content=bumped.encode()))
    out2 = run_once(config, now=NOW + timedelta(minutes=30), transport=net2.transport, sleep=clock.sleep,
                    monotonic=clock, deadline_sec=10**6)
    payload = out2.payload
    assert payload["cold_start"] is False
    assert payload["rank_change_basis_at"] == "2026-10-14T09:00:00Z"
    assert any(it["prev_rank"] is not None for it in payload["items"])
    lsf = next(it for it in payload["items"] if it["id"] == "le-sserafim")
    assert lsf["metrics"]["yt_views_per_hour_estimated"] is False
    # observed_at includes per-request fetch offsets, so the pair span is ~30 min, not exactly 30 min.
    assert 120_000 < lsf["metrics"]["yt_views_per_hour"] < 140_000
