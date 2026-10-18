"""Data freshness (spec §3.4, §3.7, §5.3, §5.4): a stale day is never "current", never scored, and never makes
English Wikipedia read as "quiet". Live 2026-10-18: Wikimedia published no daily pageviews after 2026-10-15 for
3+ days; the source still read `ok` and the Naver Korea-first rule called stale English Wikipedia "EN quiet"."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
import pytest

from seoulpulse.config import load_config
from seoulpulse.fetchers.base import RunContext, SearchRow, StoreView
from seoulpulse.fetchers.wikipedia import WikipediaFetcher
from seoulpulse.http import HttpClient
from seoulpulse.publish import item_metrics
from seoulpulse.scoring import (
    ArtistInfo,
    SearchSignal,
    en_wiki_quiet,
    score_artist,
    search_signal,
    wiki_day_fresh,
    wiki_expected_day,
    wiki_lang_signal,
)
from seoulpulse.seed import Alias, Artist, Seed
from seoulpulse.why import template_why

from .conftest import FakeClock, utc
from .fakenet import wiki_flat

D = date(2026, 10, 15)                       # the last day Wikimedia published in the live incident
LIVE = utc(2026, 10, 18, 23, 15, 0)          # 3 days later: expected day 10-17, D trails it by 2 → stale
FRESH = utc(2026, 10, 16, 9, 0, 0)           # D is yesterday: current


def series(base: int, x: int, end: date = D) -> dict[date, int]:
    s = {end - timedelta(days=k): base for k in range(1, 15)}
    s[end] = x
    return s


def score(now, *, ko=None, en=None, wiki_en="X", wiki_ko="엑스", search=None):
    info = ArtistInfo("x", "X", None, wiki_en=wiki_en, wiki_ko=wiki_ko)
    wiki = {}
    if ko is not None:
        wiki[("ko", wiki_ko)] = ko
    if en is not None:
        wiki[("en", wiki_en)] = en
    s = score_artist(info, (), wiki, [], now, None, search)
    s.why, s.extra["why_rule"] = template_why(s, now)
    return s


# ---------------------------------------------------------------------------- the freshness window


def test_expected_day_and_window():
    assert wiki_expected_day(date(2026, 10, 18)) == date(2026, 10, 17)
    assert wiki_day_fresh(D, date(2026, 10, 16))          # yesterday
    assert wiki_day_fresh(D, date(2026, 10, 17))          # one day behind: before 01-06 UTC, or a short delay
    assert not wiki_day_fresh(D, date(2026, 10, 18))      # two days behind: stale
    assert wiki_lang_signal("en", "X", series(1000, 1000), date(2026, 10, 17)).missing is False
    stale = wiki_lang_signal("en", "X", series(1000, 1000), date(2026, 10, 18))
    assert (stale.missing, stale.reason, stale.day) == (True, "stale", D)


# ---------------------------------------------------------------------------- scoring with a stale wiki day


def test_stale_wiki_scores_nothing_and_says_nothing():
    """Same pageviews (KO 5x spike, EN flat): fresh → Korea-first R3 with wiki_day; 3 days later → nothing."""
    ko, en = series(1000, 5000), series(1000, 1000)
    fresh = score(FRESH, ko=ko, en=en)
    assert fresh.ko_lead_wiki and fresh.korea_first and fresh.s_wiki > 0
    assert (fresh.why, fresh.extra["why_rule"]) == ("Korean Wikipedia views 5.0x normal, EN quiet", "R3")
    m = item_metrics(fresh)
    assert (m["wiki_day"], m["wiki_ko_ratio"], m["wiki_en_ratio"]) == ("2026-10-15", 5.0, 1.0)

    stale = score(LIVE, ko=ko, en=en)
    assert stale.wiki_ko.reason == "stale" and stale.wiki_en.reason == "stale"
    assert stale.wiki_z == 0.0 and stale.s_wiki == 0.0
    assert not stale.ko_lead_wiki and not stale.korea_first
    assert stale.extra["why_rule"] not in ("R3", "R5") and "Wikipedia" not in stale.why
    m = item_metrics(stale)
    assert m["wiki_day"] is None                          # the numbers are not used, so no day is claimed
    assert all(m[k] is None for k in ("wiki_ko_views", "wiki_ko_ratio", "wiki_en_views", "wiki_en_ratio"))


@pytest.mark.parametrize(("en", "wiki_en", "quiet"), [
    (series(1000, 1000), "X", True),                                   # fresh, z 0 → quiet
    (series(1000, 1600), "X", False),                                  # fresh, z 2.4 → loud
    (series(1000, 1000, end=D - timedelta(days=2)), "X", None),        # stale → unknown
    (None, "X", None),                                                 # article never answered → unknown
    ({D - timedelta(days=k): 1000 for k in range(0, 4)}, "X", None),   # short baseline → unknown
    (None, None, True),                                                # no English article at all → quiet
])
def test_korean_wiki_lead_needs_known_quiet_english(en, wiki_en, quiet):
    s = score(utc(2026, 10, 16, 9), ko=series(1000, 5000), en=en, wiki_en=wiki_en)
    assert en_wiki_quiet(s.wiki_en) is bool(quiet)
    assert s.ko_lead_wiki is bool(quiet) and s.korea_first is bool(quiet)
    if quiet:
        assert (s.why, s.extra["why_rule"]) == ("Korean Wikipedia views 5.0x normal, EN quiet", "R3")
    else:                                                  # the KO spike is still real: R5, without "EN quiet"
        assert (s.why, s.extra["why_rule"]) == ("Korean Wikipedia views 5.0x the 2-week avg", "R5")


# ---------------------------------------------------------------------------- Naver spike vs stale EN


SPIKE = SearchSignal(False, "", day=date(2026, 10, 18), z=3.6, ratio=1.9, s_search=0.6)


def test_search_spike_with_stale_english_wikipedia_is_not_korea_first():
    s = score(LIVE, en=series(1000, 1000), search=SPIKE, wiki_ko=None)
    assert s.wiki_en.reason == "stale" and s.s_search == 0.6
    assert not s.ko_lead_search and not s.korea_first
    assert (s.why, s.extra["why_rule"]) == ("Korean searches 1.9x normal", "R5S")


def test_search_spike_with_fresh_quiet_english_is_unchanged():
    s = score(utc(2026, 10, 16, 23, 15), en=series(1000, 1000), search=SPIKE, wiki_ko=None)
    assert s.ko_lead_search and s.korea_first
    assert (s.why, s.extra["why_rule"]) == ("Korean searches 1.9x normal, EN quiet", "R3S")


def test_stale_search_row_is_dropped_at_scoring_time():
    """A Naver row is used while D ≥ target − 2 d (target = KST yesterday); older, it adds nothing."""
    target = date(2026, 10, 18)                        # LIVE is 2026-10-19 08:15 KST

    def row(day):
        return SearchRow("x", target, False, day, 5.2, 2.3, 14, "", LIVE)

    used = search_signal(row(target - timedelta(days=2)), LIVE)
    assert not used.missing and used.s_search > 0
    dropped = search_signal(row(target - timedelta(days=3)), LIVE)
    assert (dropped.missing, dropped.reason, dropped.s_search) == (True, "stale", 0.0)
    s = score(LIVE, en=series(1000, 1000, end=date(2026, 10, 17)), search=dropped, wiki_ko=None)
    assert s.s_search == 0.0 and not s.korea_first and "searches" not in s.why


# ---------------------------------------------------------------------------- source status


def _seed(n: int) -> Seed:
    return Seed(artists=tuple(
        Artist(id=f"a{i}", name=f"A{i}", name_ko=None, aliases=(Alias(f"Artist{i}", False),), youtube=(),
               wiki_en=f"Art{i}", wiki_ko=None) for i in range(n)), channels=())


def _fetch(now, handler, latest: date | None):
    clock = FakeClock()
    http = HttpClient("SeoulPulse/test", transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                      rand=lambda: 0.0)
    cfg = load_config({"SP_CONTACT": "https://example.invalid/t"}, warn=False)
    seed = _seed(2)
    view = StoreView(wiki_latest={("en", a.wiki_en): latest for a in seed.artists} if latest else {})
    ctx = RunContext(now=now, http=http, seed=seed, config=cfg, store=view, log=logging.getLogger("t"),
                     monotonic=clock)
    return WikipediaFetcher().fetch(ctx)


def through(last: date):
    """Wikimedia while it has published nothing after `last`: a range answer simply stops at `last`."""
    return wiki_flat(views=100, through=last)


def test_upstream_delay_degrades_the_wikipedia_source():
    out = _fetch(LIVE, through(D), latest=D)
    r = out.result
    assert (r.status, r.units_ok, r.units_total) == ("degraded", 2, 2)          # every request answered
    assert r.detail == "no Wikimedia data after 2026-10-15 (upstream delay) · 2 articles"
    assert r.errors[0] == "no Wikimedia data after 2026-10-15: newest day trails 2026-10-17 by 2 days"


def test_one_day_behind_is_still_ok():
    out = _fetch(utc(2026, 10, 17, 12), through(D), latest=D)
    assert (out.result.status, out.result.detail) == ("ok", "0/2 articles current (day 2026-10-15)")
    assert out.result.errors == []


def test_stale_and_unreachable_is_an_error_not_an_upstream_delay():
    out = _fetch(LIVE, lambda req: httpx.Response(500), latest=D)
    assert out.result.status == "error"
    assert out.result.detail == "no Wikimedia data after 2026-10-15 · 0/2 articles"
