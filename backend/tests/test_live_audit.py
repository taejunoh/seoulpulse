"""Regression tests for the live-feed data-quality audit (2026-10-15, ~11 h after the YouTube API key went live).

Each test names the live symptom it guards against.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from seoulpulse.fetchers.base import ApiSnapshot, Headline, VideoObs
from seoulpulse.matching import AliasMatcher, classify_video
from seoulpulse.pipeline import run_once
from seoulpulse.scoring import Mention, Snapshot, Video, calc_video, news_signal, velocity_pair
from seoulpulse.seed import load_seed
from seoulpulse.store import Store

from .conftest import DATA_DIR, FakeClock, utc
from .fakenet import FakeNet
from .test_youtube_api import DERIVED, KEY, FakeYouTubeApi

NOW = utc(2026, 10, 14, 9, 0, 0)
W = "https://www.youtube.com/watch?v=AAAAAAAAAAA"


def h(hours: float):
    return NOW - timedelta(hours=hours)


def mv(*snaps: Snapshot) -> Video:
    return Video("LIVEAUDIT01", "meovv", "mv", "MEOVV(미야오) - ‘GO HARD’ M/V", NOW - timedelta(days=2), snaps)


# ------------------------------------------------------------------ 1. velocity never pairs RSS with API counts
# The API pass replaces the cached RSS count with the exact count, so on the first API run (and whenever the API
# falls back to RSS) the newest snapshot comes from a different counter than the ones before it. Pairing across
# the two measures the gap between counters, not views.


def test_first_api_run_uses_the_rss_pair_not_an_rss_to_api_pair():
    # RSS counts trail the real counter; the first exact API count lands 50,000 views "ahead".
    v = mv(Snapshot(h(6), 1_000_000, "rss"), Snapshot(h(0.5), 1_055_000, "rss"), Snapshot(h(0), 1_110_000, "api"))
    pair, end = velocity_pair(sorted(v.snapshots, key=lambda s: s.observed_at))
    assert (pair.source, end.source) == ("rss", "rss") and end.observed_at == h(0.5)
    c = calc_video(v, NOW)
    assert c is not None and c.has_pair and not c.estimated
    assert c.vph == pytest.approx(55_000 / 5.5)          # 10,000/h, not the mixed (1,110,000-1,000,000)/6 = 18,333/h
    assert c.pair_source == "rss" and c.t_P == h(0.5) and c.V_L == 1_110_000 and c.t_L == NOW


def test_api_pair_wins_once_the_api_has_two_snapshots():
    v = mv(Snapshot(h(6), 1_000_000, "rss"), Snapshot(h(0.5), 1_100_000, "api"), Snapshot(h(0), 1_105_000, "api"))
    c = calc_video(v, NOW)
    assert c.pair_source == "api" and c.t_O == h(0.5) and c.vph == pytest.approx(10_000)


def test_api_falling_back_to_rss_keeps_the_api_pair():
    # Quota exhausted: the newest count is a (lower, cached) RSS value. Old code paired it with the 6 h-old API
    # count and read 1,000/h; the API's own pair says 10,000/h.
    v = mv(Snapshot(h(6), 1_000_000, "api"), Snapshot(h(0.5), 1_055_000, "api"), Snapshot(h(0), 1_006_000, "rss"))
    c = calc_video(v, NOW)
    assert c.pair_source == "api" and c.vph == pytest.approx(10_000) and c.V_L == 1_006_000


def test_single_source_history_is_unchanged():
    snaps = (Snapshot(h(6), 1_000_000), Snapshot(h(3), 1_030_000), Snapshot(h(0), 1_060_000))
    c = calc_video(mv(*snaps), NOW)
    assert c.t_O == h(6) and c.vph == pytest.approx(10_000) and c.pair_source == "rss"
    # No valid same-source pair at all → lifetime estimate, exactly as before.
    lone = calc_video(mv(Snapshot(h(5), 900_000, "rss"), Snapshot(h(0), 1_000_000, "api")), NOW)
    assert not lone.has_pair and lone.estimated


def test_store_hands_snapshot_sources_to_scoring(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    with st.tx():
        st.upsert_video(VideoObs("LIVEAUDIT01", "UCx", "t", W, h(48), 1_000, h(1)), "mv", "meovv")
        st.insert_api_snapshot(ApiSnapshot("LIVEAUDIT01", NOW, 2_000))
    _, snaps = st.scoring_videos(NOW - timedelta(days=30))
    assert snaps["LIVEAUDIT01"] == [(h(1), 1_000, "rss"), (NOW, 2_000, "api")]
    st.close()


# ------------------------------------------------------------------ 2. YouTube status during an RSS outage
# Live: YouTube's feed server answered 404 for every channel from ~23:50 UTC; the API kept every channel fresh,
# yet the source read `error` ("RSS 2/77 channels") with last_success_at frozen for 6 h.


def rss_404(_: httpx.Request) -> httpx.Response:
    return httpx.Response(404, content=b"<html>Error 404 (Not Found)!!1</html>")


def run(cfg, api: FakeYouTubeApi | None = None):
    """One YouTube-only pipeline run in which every RSS feed answers 404."""
    net = FakeNet(youtube=rss_404)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "www.googleapis.com":
            assert api is not None, "no API call expected"
            return api(req)
        return net.handler(req)

    clock = FakeClock()
    return run_once(cfg, now=NOW, transport=httpx.MockTransport(handler), sleep=clock.sleep, monotonic=clock,
                    rand=lambda: 0.0, deadline_sec=10**6, sources=["youtube"])


def test_rss_outage_bridged_by_the_api_is_not_an_error(config):
    cfg = replace(config, youtube_api_key=KEY)
    out = run(cfg, FakeYouTubeApi())
    res = out.results["youtube"]
    assert res.status == "ok" and (res.units_ok, res.units_total) == (77, 77)
    assert res.detail == "RSS 0/77 + API 77 channels · 78 units (78/9000 today)"
    src = next(s for s in out.payload["sources"] if s["id"] == "youtube")
    assert src["status"] == "ok" and src["last_success_at"] is not None


def test_channels_the_api_cannot_read_still_count_as_failed(config):
    cfg = replace(config, youtube_api_key=KEY)
    out = run(cfg, FakeYouTubeApi(missing={DERIVED}))        # HYBE LABELS: no usable uploads playlist
    res = out.results["youtube"]
    assert res.status == "degraded" and res.units_ok == 76
    assert res.detail.startswith("RSS 0/77 + API 76 channels") and "1 channel RSS-only" in res.detail


def test_keyless_rss_outage_is_still_an_error(config):
    out = run(config)
    assert out.results["youtube"].status == "error" and out.results["youtube"].detail == "RSS 0/77 channels"


# ------------------------------------------------------------------ 3. korea_first needs English coverage
# Live: BTS showed "Korean press led English by 20h" (+6 KR-first bonus, rank 1) for 10+ hours. Its oldest
# Korean mention (a Yonhap backlog item 38 h before the first run) predated the English feeds' backlog, so the
# English coverage it was compared against had never been collected.


def m(lang: str, source: str, hours: float, i: int) -> Mention:
    return Mention("bts", f"hl{i}", source, lang, "t", "u", h(hours))


BTS = [m("ko", "yonhap", 38, 1), m("en", "soompi", 17, 2), m("ko", "yonhap", 15, 3), m("en", "koreaboo", 6, 4)]


def test_korean_mention_older_than_english_coverage_cannot_lead():
    old = news_signal(BTS, NOW, None)                          # unrestricted: the false lead seen live
    assert old.ko_lead and old.ko_lead_hours == 21
    fixed = news_signal(BTS, NOW, None, en_coverage_from=h(40))   # English history starts 40 h ago
    assert not fixed.ko_lead                                    # Yonhap 15 h ago came after Soompi 17 h ago
    assert fixed.headlines_24h == 3 and fixed.raw == old.raw    # counts and S_news are untouched


def test_genuine_korean_lead_inside_english_coverage_still_flags():
    ms = [m("ko", "yonhap", 5, 1)]
    assert news_signal(ms, NOW, None, en_coverage_from=h(40)).ko_no_en
    lead = news_signal([m("ko", "yonhap", 14, 1), m("en", "soompi", 3, 2)], NOW, None, en_coverage_from=h(40))
    assert lead.ko_lead and lead.ko_lead_hours == 11


def test_no_english_headlines_at_all_means_no_news_based_korea_first(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    assert st.earliest_headline_at("en") is None
    with st.tx():
        st.insert_headline(Headline("h1", "soompi", "en", "t", "u", "g1", h(30), h(1)))
        st.insert_headline(Headline("h2", "soompi", "en", "t", "u", "g2", h(50), h(1)))
        st.insert_headline(Headline("h3", "yonhap", "ko", "t", "u", "g3", h(70), h(1)))
    assert st.earliest_headline_at("en") == h(50) and st.earliest_headline_at("ko") == h(70)
    st.close()
    # The pipeline passes `now` when nothing English is stored: no Korean mention can satisfy the lookback.
    assert not news_signal([m("ko", "yonhap", 2, 1)], NOW, None, en_coverage_from=NOW).ko_lead


# ------------------------------------------------------------------ 4. Yonhap's mixed-script NCT unit names


def test_yonhap_mixed_script_nct_units_match():
    matcher = AliasMatcher(load_seed(DATA_DIR).artists)
    # Both are live Yonhap headlines (2026-10-15) that NCT WISH never received.
    assert matcher.mentions("[가요소식] NCT 위시, 신보 발매 하루 만에 밀리언셀러") == {"nct-wish"}
    assert matcher.mentions("[방송소식] 코르티스·NCT 위시, '2026 마마 어워즈' 출연") == {"cortis", "nct-wish"}
    assert matcher.mentions("NCT 드림이 컴백했다") == {"nct-dream"}
    assert matcher.mentions("NCT 127 drops a new album") == {"nct-127"}


# ------------------------------------------------------------------ 5. stage videos are performances


@pytest.mark.parametrize(("title", "kind"), [
    ("Stray Kids World Tour ＜RUN IT SEOUL＞ \"RUN IT\" Stage Video", "perf"),                 # live: was "other"
    ("‘セツナハナビ (Setsuna Hanabi)’ stage @ CDTVライブ！ライブ！260831 | TXT (투모로우바이투게더)", "perf"),
    ("KickFlip 'Kick Off The Wall' Comeback Stage | M COUNTDOWN 261009", "show"),                # show still wins
    ("‘BOOMPALA’ Stage Cam @ iHeartRadio Music Festival 2026", "perf"),                          # live: was "other"
    ("[GOING JxJ SPECIAL] THE DREAM GAME SHOW #1", "other"),
    ("aespa backstage vlog", "other"),
])
def test_stage_videos_classify_as_performances(title, kind):
    assert classify_video(title, W) == kind


@pytest.mark.parametrize(("title", "kind"), [
    ("ILLIT (아일릿) ‘BREAK EVEN’ Teasing Video", "teaser"),                    # live top video, was "other"
    ("[TIME TO TWICE] 트둥의 숲 TDOONG's Crossing Teaser", "other"),            # live: "comeback incoming"
    ("[GOING SEVENTEEN] EP.170 Trailer", "other"),
    ("TWICE 'Sample' M/V Teaser", "teaser"),                                    # real comeback teasers unchanged
    ("Members keep teasing each other | aespa vlog", "other"),
])
def test_comeback_teasers_vs_variety_teasers(title, kind):
    assert classify_video(title, W) == kind
