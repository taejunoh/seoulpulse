"""Spec §5.8 worked example (normative): every value must reproduce. S terms ±0.001, scores exactly."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from seoulpulse.publish import build_item
from seoulpulse.scoring import (
    ArtistInfo,
    Mention,
    Snapshot,
    Video,
    apply_rank_changes,
    rank_scores,
    score_all,
)
from seoulpulse.why import template_why

from .conftest import EXAMPLE_PATH, utc

NOW = utc(2026, 10, 14, 9, 0, 0)
FIRST_RUN = NOW - timedelta(days=6)          # hist_days = min(7, 5) = 5
D = date(2026, 10, 13)
LANG = {"yonhap": "ko", "soompi": "en", "koreaboo": "en"}

ARTISTS = [
    ArtistInfo("kiss-of-life", "KISS OF LIFE", "키스오브라이프", wiki_en="Kiss of Life (group)", wiki_ko="키스오브라이프"),
    ArtistInfo("aespa", "aespa", "에스파", wiki_en="Aespa", wiki_ko="Aespa"),
    ArtistInfo("seventeen", "SEVENTEEN", "세븐틴", wiki_en="Seventeen (South Korean band)", wiki_ko="세븐틴 (음악 그룹)"),
]


def at(day: int, hour: int, minute: int = 0):
    return utc(2026, 10, day, hour, minute)


def series(baseline: list[int], x: int) -> dict[date, int]:
    assert len(baseline) == 14
    out = {D - timedelta(days=14 - i): v for i, v in enumerate(baseline)}
    out[D] = x
    return out


WIKI = {
    ("ko", "키스오브라이프"): series([400, 380, 420, 410, 390, 400, 405, 395, 400, 410, 390, 400, 400, 400], 1300),
    ("en", "Kiss of Life (group)"): series(
        [1500, 1450, 1550, 1500, 1480, 1520, 1500, 1500, 1490, 1510, 1500, 1500, 1500, 1500], 1560),
    ("ko", "Aespa"): series(
        [3100, 2900, 3000, 3200, 2800, 3050, 2950, 3100, 3000, 2900, 3150, 2850, 3000, 3000], 9000),
    ("en", "Aespa"): series(
        [8000, 7600, 8400, 8100, 7900, 8200, 7800, 8000, 8300, 7700, 8100, 7900, 8000, 8000], 13600),
    ("ko", "세븐틴 (음악 그룹)"): series([5000] * 14, 5200),
    ("en", "Seventeen (South Korean band)"): series(
        [20000, 21000, 19000, 20500, 19500, 20000, 20000, 21000, 19000, 20000, 20000, 20500, 19500, 20000], 23000),
}

VIDEOS = {
    "kiss-of-life": [
        Video("SAMPLE0001A", "kiss-of-life", "mv", "KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV", at(5, 9),
              (Snapshot(at(14, 3), 1_500_000), Snapshot(at(14, 9), 1_620_000)), channel_name="KISS OF LIFE"),
    ],
    "aespa": [
        Video("SAMPLE0002A", "aespa", "mv", "aespa 에스파 'Neon Tide' MV", at(13, 15),
              (Snapshot(at(14, 6), 4_200_000), Snapshot(at(14, 8, 30), 4_520_000), Snapshot(at(14, 9), 4_580_000)),
              channel_name="SMTOWN"),
        Video("SAMPLE0002T", "aespa", "teaser", "aespa 에스파 'Neon Tide' MV Teaser", at(10, 9),
              (Snapshot(at(14, 6), 2_950_000), Snapshot(at(14, 9), 2_962_000)), channel_name="SMTOWN"),
    ],
    "seventeen": [
        Video("SAMPLE0004A", "seventeen", "perf", "SEVENTEEN (세븐틴) 'Afterglow' Performance Film", at(12, 9),
              (Snapshot(at(14, 9), 900_000),), channel_name="HYBE LABELS"),
    ],
}


def mentions(artist_id: str, specs: list[tuple[str, float]]) -> list[Mention]:
    return [
        Mention(artist_id, f"{artist_id}-{i}", src, LANG[src], f"[Sample] {artist_id} {i}",
                "https://www.yna.co.kr/entertainment/all", NOW - timedelta(hours=age))
        for i, (src, age) in enumerate(specs)
    ]


MENTIONS = {
    # base_daily 0.2 = 1.0 / 5 days (history mention at 60 h)
    "kiss-of-life": mentions("kiss-of-life", [("yonhap", 4), ("soompi", 60)]),
    # base_daily 1.0 = (1.5·2 + 1.0·2) / 5
    "aespa": mentions("aespa", [("yonhap", 5), ("yonhap", 20), ("soompi", 3),
                                ("yonhap", 50), ("yonhap", 70), ("soompi", 90), ("soompi", 110)]),
    # base_daily 3.0 = (soompi@30h 1.0 + 6·1.5 + 5·1.0) / 5
    "seventeen": mentions("seventeen", [
        ("soompi", 2), ("soompi", 6), ("soompi", 10), ("soompi", 30), ("koreaboo", 8), ("koreaboo", 12),
        ("yonhap", 1),
        ("yonhap", 50), ("yonhap", 60), ("yonhap", 70), ("yonhap", 80), ("yonhap", 90), ("yonhap", 100),
        ("soompi", 55), ("soompi", 65), ("soompi", 75), ("soompi", 85), ("soompi", 95)]),
}

BASIS = {"aespa": 4, "seventeen": 2}


@pytest.fixture(scope="module")
def result():
    scores = score_all(ARTISTS, VIDEOS, WIKI, MENTIONS, NOW, FIRST_RUN)
    ranked = rank_scores(scores)
    apply_rank_changes(ranked, BASIS)
    for s in scores:
        s.why, _ = template_why(s, NOW)
    return {s.id: s for s in scores}, ranked


def approx(x, rel=1e-4, abs_=0.06):
    return pytest.approx(x, rel=rel, abs=abs_)


def test_kiss_of_life(result):
    s = result[0]["kiss-of-life"]
    top = s.top
    assert top.age_L == 216
    assert top.vph == pytest.approx(20_000)
    assert top.t_O == at(14, 3)
    assert top.c_life == approx(181_323.3)
    assert top.c_recent == approx(1_468_861.2)
    assert top.c_recent / top.c_life == pytest.approx(8.10, abs=0.005)
    assert top.r == 4.0
    assert top.I == approx(108_393.2)
    assert top.e == approx(108_393.2)
    assert s.yt_raw == approx(108_393.2)
    assert s.s_yt == pytest.approx(0.8140, abs=0.001)
    assert s.s_accel == pytest.approx(1.0, abs=0.001)
    assert s.g == 1.0
    assert s.s_new == 0.0
    assert (s.wiki_ko.mu, s.wiki_ko.sigma_eff, s.wiki_ko.z, s.wiki_ko.ratio) == (400, 100, 9.0, 3.25)
    assert s.wiki_en.z == pytest.approx(0.16) and s.wiki_en.ratio == pytest.approx(1.04)
    assert s.wiki_z == pytest.approx(5.464)
    assert s.s_wiki == pytest.approx(0.6830, abs=0.001)
    assert s.news.base_daily == pytest.approx(0.2)
    assert s.news.raw == pytest.approx(1.1906, abs=1e-4)
    assert s.news.excess == pytest.approx(1.0554, abs=1e-4)
    assert s.news.s_news == pytest.approx(0.2598, abs=0.001)
    assert s.ko_lead_news is True and s.news.ko_no_en is True
    assert s.ko_lead_wiki is True
    assert s.raw_score - 6 == pytest.approx(61.05, abs=0.005)
    assert s.score == 67.0
    assert (s.rank, s.prev_rank, s.change, s.change_amount) == (1, None, "new", 0)
    assert s.why == '"Lucky Seven" views up 4.0x vs. normal decay'


def test_aespa(result):
    s = result[0]["aespa"]
    mv = next(c for c in s.calcs if c.video.kind == "mv")
    teaser = next(c for c in s.calcs if c.video.kind == "teaser")
    assert mv.t_O == at(14, 6)                       # oldest snapshot in [t_L−6h, t_L−25min]
    assert mv.vph == pytest.approx(126_666.7, abs=0.05)
    assert mv.age_L == 18
    assert mv.c_life == approx(1_363_367.4)
    assert mv.c_recent == approx(1_307_373.6)
    assert mv.r == pytest.approx(0.9589, abs=1e-4)
    assert mv.I == approx(96_476.4)
    assert teaser.vph == pytest.approx(4_000)
    assert teaser.I == approx(11_421.0)
    assert teaser.e == approx(3_997.3)
    assert s.top is mv
    assert s.yt_raw == approx(98_475.1)
    assert s.s_yt == pytest.approx(0.7973, abs=0.001)
    assert s.s_accel == pytest.approx(-0.0303, abs=0.001)
    assert s.g == 1.0
    assert s.s_new == pytest.approx(0.6250, abs=0.001)
    assert (s.wiki_ko.mu, s.wiki_ko.sigma_eff, s.wiki_ko.z, s.wiki_ko.ratio) == (3000, 750, 8.0, 3.0)
    assert (s.wiki_en.sigma_eff, s.wiki_en.ratio) == (2000, 1.7)
    assert s.wiki_en.z == pytest.approx(2.8)
    assert s.wiki_z == pytest.approx(5.92)
    assert s.s_wiki == pytest.approx(0.7400, abs=0.001)
    assert s.news.base_daily == pytest.approx(1.0)
    assert s.news.raw == pytest.approx(2.4371, abs=1e-4)
    assert s.news.excess == pytest.approx(1.7611, abs=1e-4)
    assert s.news.s_news == pytest.approx(0.3663, abs=0.001)
    assert s.ko_lead_news is True and s.news.ko_lead_hours == 17      # EN 17 h later
    assert s.ko_lead_wiki is False                                    # z_en 2.8
    assert s.raw_score - 6 == pytest.approx(57.12, abs=0.005)
    assert s.score == 63.1
    assert (s.rank, s.prev_rank, s.change, s.change_amount) == (2, 4, "up", 2)
    assert s.why == 'New MV "Neon Tide" at 127K views/hr'


def test_seventeen(result):
    s = result[0]["seventeen"]
    top = s.top
    assert top.has_pair is False
    assert top.display_vph == pytest.approx(7_583.8, abs=0.05) and top.estimated is True
    assert top.c_life == approx(173_406.1)
    assert top.c_recent is None and top.r is None
    assert top.I == approx(12_796.3)
    assert top.e == approx(7_677.8)
    assert s.yt_raw == approx(7_677.8)
    assert s.s_yt == pytest.approx(0.3541, abs=0.001)
    assert s.s_accel == 0.0
    assert s.g == pytest.approx(0.8853, abs=0.001)
    assert s.s_new == 0.0
    assert s.wiki_ko.sigma_eff == 1250 and s.wiki_ko.z == pytest.approx(0.16) and s.wiki_ko.ratio == pytest.approx(1.04)
    assert s.wiki_en.sigma_eff == 5000 and s.wiki_en.z == pytest.approx(0.6) and s.wiki_en.ratio == pytest.approx(1.15)
    assert s.wiki_z == pytest.approx(0.336)
    assert s.s_wiki == pytest.approx(0.0420, abs=0.001)
    assert s.news.base_daily == pytest.approx(3.0)
    assert s.news.raw == pytest.approx(4.6558, abs=1e-4)
    assert s.news.excess == pytest.approx(2.6278, abs=1e-4)
    assert s.news.s_news == pytest.approx(0.4648, abs=0.001)
    assert s.ko_lead_news is False and s.ko_lead_wiki is False      # EN 12 h before KO
    assert s.korea_first is False
    assert s.score == 20.2
    assert (s.rank, s.prev_rank, s.change, s.change_amount) == (3, 2, "down", 1)
    assert s.why == "6 headlines in 24h (1 Korean)"


def test_ranking_order(result):
    _, ranked = result
    assert [s.id for s in ranked] == ["kiss-of-life", "aespa", "seventeen"]


def test_published_items_match_example_payload(result):
    """metrics, signals, top_video, image_url and the headline numbers equal docs/trends.example.json."""
    example = {it["id"]: it for it in json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))["items"]}
    scores, _ = result
    for aid in ("kiss-of-life", "aespa", "seventeen"):
        item = build_item(scores[aid], None)
        ex = example[aid]
        for key in ("score", "korea_first", "why", "why_source", "image_url", "metrics", "signals", "top_video"):
            assert item[key] == ex[key], (aid, key, item[key], ex[key])
    for aid in ("kiss-of-life", "aespa"):
        item = build_item(scores[aid], None)
        for key in ("rank", "prev_rank", "change", "change_amount"):
            assert item[key] == example[aid][key], (aid, key)
