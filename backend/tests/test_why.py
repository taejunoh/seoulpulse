from __future__ import annotations

from datetime import date, timedelta

import pytest

from seoulpulse import why as why_mod
from seoulpulse.scoring import ArtistInfo, Mention, Snapshot, Video, score_artist
from seoulpulse.why import fmt, template_why

from .conftest import utc

NOW = utc(2026, 10, 14, 9, 0, 0)
D = date(2026, 10, 13)
LANG = {"yonhap": "ko", "soompi": "en", "koreaboo": "en"}


def vid(vid_id, kind, title, age_h, snaps):
    return Video(vid_id, "x", kind, title, NOW - timedelta(hours=age_h),
                 tuple(Snapshot(NOW - timedelta(hours=h), v) for h, v in snaps))


def wiki_series(base: int, x: int) -> dict[date, int]:
    s = {D - timedelta(days=k): base for k in range(1, 15)}
    s[D] = x
    return s


def ment(src, age_h, i=0):
    return Mention("x", f"h{src}{age_h}{i}", src, LANG[src], "t", "https://example.invalid/",
                   NOW - timedelta(hours=age_h))


def score(videos=(), wiki=None, mentions=(), wiki_en=None, wiki_ko=None):
    info = ArtistInfo("x", "X", None, wiki_en=wiki_en, wiki_ko=wiki_ko)
    return score_artist(info, videos, wiki or {}, list(mentions), NOW, None)


def why(s):
    return template_why(s, NOW)


def test_fmt_table():
    assert fmt(999) == "999"
    assert fmt(7583.8) == "7.6K"
    assert fmt(7000) == "7K"
    assert fmt(126_666.7) == "127K"
    assert fmt(1_250_000) == "1.2M"
    assert fmt(2_000_000) == "2M"
    assert fmt(9_960) == "10K"
    assert fmt(999_700) == "1M"


def test_r1_new_mv_with_song():
    s = score([vid("AAAAAAAAAA1", "mv", "aespa 에스파 'Neon Tide' MV", 18, [(3, 4_200_000), (0, 4_580_000)])])
    assert why(s) == ('New MV "Neon Tide" at 127K views/hr', "R1")


def test_r1_no_song_form():
    s = score([vid("AAAAAAAAAA1", "mv", "aespa Official MV", 18, [(3, 4_200_000), (0, 4_580_000)])])
    assert why(s) == ("New MV out, 127K views/hr", "R1")


def test_r1_korean_song_title():
    s = score([vid("AAAAAAAAAA1", "mv", "[MV] 아이유 '밤편지' Official MV", 10, [(3, 100_000), (0, 130_000)])])
    assert why(s) == ('New MV "밤편지" at 10K views/hr', "R1")


def test_r1_long_song_truncated_and_within_60():
    title = "ARTIST 'An Unreasonably Long Song Title For Testing Purposes' MV"
    s = score([vid("AAAAAAAAAA1", "mv", title, 10, [(3, 100_000), (0, 130_000)])])
    text, rule = why(s)
    assert rule == "R1"
    assert "…" in text and len(text) <= 60


def test_r2_accel_with_song():
    s = score([vid("AAAAAAAAAA1", "mv", "KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV", 216,
                   [(6, 1_500_000), (0, 1_620_000)])])
    assert why(s) == ('"Lucky Seven" views up 4.0x vs. normal decay', "R2")


def test_r2_no_song_video_form():
    s = score([vid("AAAAAAAAAA1", "perf", "KISS OF LIFE Dance Practice", 216, [(6, 1_500_000), (0, 1_620_000)])])
    assert why(s) == ("Video views up 4.0x vs. normal decay", "R2")


def test_r3_korean_wiki_lead():
    s = score(wiki={("ko", "엑스"): wiki_series(1000, 5000)}, wiki_ko="엑스")
    assert s.ko_lead_wiki
    assert why(s) == ("Korean Wikipedia views 5.0x normal, EN quiet", "R3")


def test_r4_korean_news_no_english():
    s = score(mentions=[ment("yonhap", 2), ment("yonhap", 5)])
    assert why(s) == ("2 Korean headlines, no English coverage yet", "R4")
    s1 = score(mentions=[ment("yonhap", 2)])
    assert why(s1) == ("1 Korean headline, no English coverage yet", "R4")


def test_r4_korean_press_led_by_hours():
    s = score(mentions=[ment("yonhap", 20), ment("soompi", 3)])
    assert why(s) == ("Korean press led English by 17h", "R4")


def test_r5_english_wiki():
    s = score(wiki={("en", "X"): wiki_series(1000, 3000)}, wiki_en="X")
    assert why(s) == ("English Wikipedia views 3.0x the 2-week avg", "R5")


def test_r5_korean_wins_when_higher_and_en_not_quiet():
    s = score(wiki={("ko", "엑스"): wiki_series(1000, 5000), ("en", "X"): wiki_series(1000, 1400)},
              wiki_ko="엑스", wiki_en="X")
    assert not s.ko_lead_wiki          # z_en = 1.6 ≥ 1.0
    assert why(s) == ("Korean Wikipedia views 5.0x the 2-week avg", "R5")


def test_r6_news_counts():
    s = score(mentions=[ment("soompi", 1), ment("koreaboo", 2), ment("soompi", 3, 1)])
    assert why(s) == ("3 headlines in 24h", "R6")
    s2 = score(mentions=[ment("soompi", 10), ment("yonhap", 2)])   # EN before KO → not Korea-first
    assert why(s2) == ("2 headlines in 24h (1 Korean)", "R6")


def test_r7_teaser():
    s = score([vid("AAAAAAAAAA1", "teaser", "NMIXX 'Strange Muse' Official Teaser", 10, [(0, 50_000)])])
    assert why(s) == ('New teaser "Strange Muse": comeback incoming', "R7")
    s2 = score([vid("AAAAAAAAAA1", "teaser", "NMIXX Official Teaser", 10, [(0, 50_000)])])
    assert why(s2) == ("New teaser out: comeback incoming", "R7")


def test_r8_momentum():
    s = score([vid("AAAAAAAAAA1", "perf", "SEVENTEEN (세븐틴) 'Afterglow' Performance Film", 48, [(0, 900_000)])])
    assert why(s) == ('"Afterglow" steady at 7.6K views/hr', "R8")
    s2 = score([vid("AAAAAAAAAA1", "perf", "SEVENTEEN Performance Film", 48, [(0, 900_000)])])
    assert why(s2) == ("Steady YouTube pull: 7.6K views/hr", "R8")


def test_r9_fallback():
    assert why(score()) == ("Buzzing across Korean sources", "R9")


def test_render_enforces_60_code_points_and_falls_back():
    long_song = "S" * 24
    assert why_mod._render(long_song, lambda s: "x" * 61, "short form") == "short form"
    assert why_mod._render(long_song, lambda s: "x" * 61, "y" * 61) is None
    assert why_mod._render(None, lambda s: "unused", "no song") == "no song"


@pytest.mark.parametrize("age", [1, 30, 47])
def test_every_rule_output_fits(age):
    s = score([vid("AAAAAAAAAA1", "mv", "X '" + "가" * 40 + "' MV", age, [(1, 10_000), (0, 999_999_999)])])
    text, _ = why(s)
    assert 1 <= len(text) <= 60


# R8 wording follows the top video's acceleration r. Live 2026-10-15: Rosé, ALLDAY PROJECT and KickFlip read
# '"…" steady at X views/hr' while their r sat at the 0.25 floor (views slowing sharply). Now r < 0.7 reads
# "still pulling", 0.7 ≤ r < 1.5 (or no r) stays "steady", and r ≥ 1.5 uses R2's "views up" wording.
AFTERGLOW = "SEVENTEEN (세븐틴) 'Afterglow' Performance Film"


def perf(snaps, title=AFTERGLOW):
    return score([vid("AAAAAAAAAA1", "perf", title, 48, snaps)])


def test_r8_slowing_video_is_still_pulling_not_steady():
    s = perf([(6, 900_000), (0, 902_000)])
    assert s.r_top == 0.25
    assert why(s) == ('"Afterglow" still pulling 333 views/hr', "R8")
    s2 = perf([(6, 900_000), (0, 902_000)], title="SEVENTEEN Performance Film")
    assert why(s2) == ("Still pulling 333 views/hr on YouTube", "R8")


def test_r8_normal_decay_is_steady():
    s = perf([(6, 900_000), (0, 950_000)])
    assert 0.7 <= s.r_top < 1.5
    assert why(s) == ('"Afterglow" steady at 8.3K views/hr', "R8")


def test_r8_speeding_up_uses_the_views_up_line():
    s = perf([(6, 900_000), (0, 1_000_000)])                 # r 1.88: below R2's 2.0
    assert 1.5 <= s.r_top < 2.0
    assert why(s) == ('"Afterglow" views up 1.9x vs. normal decay', "R8")
    s2 = perf([(6, 900_000), (0, 1_000_000)], title="SEVENTEEN Performance Film")
    assert why(s2) == ("Video views up 1.9x vs. normal decay", "R8")
    small = perf([(6, 20_000), (0, 23_000)])                 # r 2.5 but S_yt < 0.2: R2 skips it, R8 says it
    assert small.r_top >= 2.0 and small.s_yt < 0.2
    assert why(small) == ('"Afterglow" views up 2.5x vs. normal decay', "R8")


@pytest.mark.parametrize(("r", "expected"), [
    (0.25, '"Afterglow" still pulling 8.3K views/hr'),
    (0.699, '"Afterglow" still pulling 8.3K views/hr'),
    (0.7, '"Afterglow" steady at 8.3K views/hr'),
    (1.499, '"Afterglow" steady at 8.3K views/hr'),
    (1.5, '"Afterglow" views up 1.5x vs. normal decay'),
    (None, '"Afterglow" steady at 8.3K views/hr'),
])
def test_r8_band_edges(r, expected):
    s = perf([(6, 900_000), (0, 950_000)])
    s.r_top = r
    assert why(s) == (expected, "R8")
