from __future__ import annotations

import pytest

from seoulpulse.matching import AliasMatcher, alias_regex, auto_case_sensitive, normalize
from seoulpulse.seed import Alias, Artist, check_alias_rules, load_seed

from .conftest import DATA_DIR


def artist(aid: str, *aliases, case: set[str] | None = None) -> Artist:
    case = case or set()
    return Artist(
        id=aid, name=aid, name_ko=None,
        aliases=tuple(Alias(a, a in case or auto_case_sensitive(a)) for a in aliases),
        youtube=(), wiki_en=None, wiki_ko=None,
    )


@pytest.fixture(scope="module")
def roster_matcher() -> AliasMatcher:
    return AliasMatcher(load_seed(DATA_DIR).artists)


def test_normalize_nfkc_and_whitespace():
    assert normalize("  ＩＶＥ　\n 𝟬𝟳 ") == "IVE 07"


def test_ive_does_not_match_five_or_ive_contraction(roster_matcher):
    assert "ive" not in roster_matcher.mentions("Five members share their I've-been-there stories")
    assert "ive" not in roster_matcher.mentions("five reasons")
    assert "ive" in roster_matcher.mentions("IVE returns with a new single")
    assert "ive" in roster_matcher.mentions("IVE의 신곡")  # Hangul may follow a Latin alias directly


def test_auto_case_sensitivity_rule():
    for term in ("IU", "IVE", "BTS", "TWS", "EXO", "ITZY", "SVT"):
        assert auto_case_sensitive(term), term
    for term in ("NCT 127", "Lisa", "aespa", "KISS OF LIFE"):
        assert not auto_case_sensitive(term), term


def test_nct_127_wins_over_nct():
    m = AliasMatcher([artist("nct", "NCT"), artist("nct-127", "NCT 127"), artist("nct-dream", "NCT DREAM")])
    assert m.mentions("NCT 127 drops a new album") == {"nct-127"}
    assert m.mentions("NCT DREAM and NCT 127 on stage") == {"nct-dream", "nct-127"}
    assert m.mentions("NCT announces a new unit") == {"nct"}
    # Whitespace inside an alias matches \s+.
    assert m.mentions("NCT   127 tour") == {"nct-127"}


def test_hangul_particles_and_boundaries(roster_matcher):
    assert "newjeans" in roster_matcher.mentions("뉴진스가 컴백했다")
    assert "newjeans" in roster_matcher.mentions("[단독] 뉴진스, 새 앨범")
    assert "jimin" not in roster_matcher.mentions("배우 한지민, 드라마 출연")
    assert "rose" not in roster_matcher.mentions("로제떡볶이 매장 오픈")
    assert "rose" in roster_matcher.mentions("로제는 오늘 신곡을 공개했다")
    assert "aespa" in roster_matcher.mentions("에스파에게 쏟아진 관심")
    assert "aespa" in roster_matcher.mentions("aespa는 오늘")


def test_particle_followed_by_hangul_does_not_match():
    m = AliasMatcher([artist("x", "지민")])
    assert m.mentions("지민이 웃었다") == {"x"}
    assert m.mentions("지민이다") == set()          # particle followed by Hangul → no match
    assert m.mentions("지민") == {"x"}


def test_case_sensitive_english_word_names(roster_matcher):
    assert "treasure" not in roster_matcher.mentions("A National Treasure Of A Drama")
    assert "treasure" in roster_matcher.mentions("TREASURE wins first place")
    assert "twice" not in roster_matcher.mentions("She won twice this year")
    assert "twice" in roster_matcher.mentions("TWICE announces tour")


def test_accented_latin_boundary():
    m = AliasMatcher([artist("rose", "Rosé")])
    assert m.mentions("ROSÉ tops the chart") == {"rose"}
    assert m.mentions("Rosé의 신곡") == {"rose"}
    assert m.mentions("Rosée morning") == set()


def test_multi_artist_headline(roster_matcher):
    got = roster_matcher.mentions("[Fixture] TWICE & NewJeans Top A Chart With aespa And IVE")
    assert got == {"twice", "newjeans", "aespa", "ive"}
    got_ko = roster_matcher.mentions("에스파·아이브·르세라핌, 시상식 동반 참석")
    assert got_ko == {"aespa", "ive", "le-sserafim"}


def test_one_headline_counts_once_per_artist(roster_matcher):
    assert roster_matcher.mentions("aespa, aespa, 에스파!") == {"aespa"}


def test_video_primary_is_earliest_match(roster_matcher):
    assert roster_matcher.primary("정한X조슈아 (SEVENTEEN) feat. aespa 'Song' MV") == "seventeen"
    assert roster_matcher.primary("aespa X SEVENTEEN 'Song' Special Video") == "aespa"
    assert roster_matcher.primary("[MV] Unknown Singer _ Song") is None


def test_deny_list_rejects_ambiguous_korean_alias():
    errors = check_alias_rules([artist("itzy", "ITZY", "있지")])
    assert any("deny-list" in e for e in errors)
    errors = check_alias_rules([artist("x", "진")])
    assert any("≥2 syllables" in e for e in errors)
    errors = check_alias_rules([artist("a", "Same"), artist("b", "same")])
    assert any("shared" in e for e in errors)
    errors = check_alias_rules([artist("a", "Q")])
    assert any("≥2 characters" in e for e in errors)


def test_committed_roster_passes_alias_rules():
    seed = load_seed(DATA_DIR)
    assert 60 <= len(seed.artists) <= 100
    assert seed.issues == ()


def test_alias_regex_rejects_empty():
    with pytest.raises(ValueError):
        alias_regex("   ", False)


def test_variety_regression_attribution(roster_matcher):
    """[EP.08] 아우디즈의 찾아서 투어 belongs to aespa only via aespa's own channel; no alias occurs in it."""
    from seoulpulse.pipeline import attribute

    title = "[EP.08] 아우디즈의 찾아서 투어✈️ | 멕시코 타코를 찾아서🇲🇽🌮"
    assert roster_matcher.matches(title) == []
    assert attribute("artist", "aespa", title, roster_matcher) == "aespa"      # own channel → owner
    assert attribute("label", None, title, roster_matcher) is None             # label channel → no match, ignored
    assert attribute(None, None, title, roster_matcher) is None                # unknown channel → ignored


def test_label_attribution_requires_bounded_alias(roster_matcher):
    from seoulpulse.pipeline import attribute

    # Substrings inside other words must not attribute (IVE in "FIVE", 에스파 inside a longer Hangul word).
    assert attribute("label", None, "FIVE STAR Sample Dance Practice", roster_matcher) is None
    assert attribute("label", None, "에스파냐 축구 하이라이트", roster_matcher) is None
    assert attribute("broadcaster", None, "[안방1열 풀캠4K] 크래비티 'LOUDER' @SBS Inkigayo", roster_matcher) == "cravity"
