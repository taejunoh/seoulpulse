from __future__ import annotations

import hashlib

import pytest

from seoulpulse.fetchers.news_rss import headline_id, parse_rss
from seoulpulse.store import Store

from .conftest import read_fixture, utc

FETCHED = utc(2026, 10, 14, 8, 20, 0)
SENTINELS = ("BODY-SENTINEL", "CONTENT-SENTINEL", "본문은", "fixture description", "article body",
             "full article text")


@pytest.mark.parametrize(("source", "lang", "fixture", "count"), [
    ("soompi", "en", "soompi.xml", 4),
    ("koreaboo", "en", "koreaboo.xml", 3),
    ("yonhap", "ko", "yonhap.xml", 4),
])
def test_parse_counts_and_lang(source, lang, fixture, count):
    items = parse_rss(read_fixture(fixture), source, lang, FETCHED)
    assert len(items) == count
    assert {h.lang for h in items} == {lang}
    assert {h.source_id for h in items} == {source}


def test_cdata_entities_and_whitespace():
    soompi = parse_rss(read_fixture("soompi.xml"), "soompi", "en", FETCHED)
    assert soompi[1].title == "[Fixture] TWICE & NewJeans Top A “Sample” Chart"
    koreaboo = parse_rss(read_fixture("koreaboo.xml"), "koreaboo", "en", FETCHED)
    assert koreaboo[0].title == "[Fixture] EXO Member’s Sample Comment Goes Viral"
    yonhap = parse_rss(read_fixture("yonhap.xml"), "yonhap", "ko", FETCHED)
    assert yonhap[0].title == "[픽스처] 뉴진스가 샘플 신곡으로 컴백…'테스트 패턴' 공개"  # Korean kept as-is


def test_pubdate_offsets_convert_to_utc():
    soompi = parse_rss(read_fixture("soompi.xml"), "soompi", "en", FETCHED)
    assert soompi[0].pub_date == utc(2026, 10, 14, 8, 11, 56)
    assert soompi[1].pub_date == utc(2026, 10, 14, 7, 30, 0)        # +0900
    yonhap = parse_rss(read_fixture("yonhap.xml"), "yonhap", "ko", FETCHED)
    assert yonhap[0].pub_date == utc(2026, 10, 14, 8, 17, 39)        # 17:17:39 +0900


def test_bad_and_future_pubdates_are_dropped():
    soompi = parse_rss(read_fixture("soompi.xml"), "soompi", "en", FETCHED)
    assert soompi[3].pub_date is None                                # "not a date"
    koreaboo = parse_rss(read_fixture("koreaboo.xml"), "koreaboo", "en", FETCHED)
    assert koreaboo[2].pub_date is None                              # > 10 minutes in the future


def test_headline_id_and_guid():
    yonhap = parse_rss(read_fixture("yonhap.xml"), "yonhap", "ko", FETCHED)
    guid = "https://www.yna.co.kr/view/AKR20261014000000001"
    assert yonhap[0].guid == guid
    assert yonhap[0].headline_id == hashlib.sha1(f"yonhap|{guid}".encode()).hexdigest()[:16]
    assert headline_id("yonhap", guid) == yonhap[0].headline_id


def test_description_and_content_never_reach_any_object_or_row(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    for source, lang, fixture in (("soompi", "en", "soompi.xml"), ("koreaboo", "en", "koreaboo.xml"),
                                  ("yonhap", "ko", "yonhap.xml")):
        items = parse_rss(read_fixture(fixture), source, lang, FETCHED)
        blob = repr(items)
        for s in SENTINELS:
            assert s not in blob, (fixture, s)
        with store.tx():
            for h in items:
                store.insert_headline(h)
    dump = "\n".join(store.conn.iterdump())
    for s in SENTINELS:
        assert s not in dump
    store.close()


def test_published_at_is_min_of_pubdate_and_first_seen(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    koreaboo = parse_rss(read_fixture("koreaboo.xml"), "koreaboo", "en", FETCHED)
    with store.tx():
        for h in koreaboo:
            store.insert_headline(h)
    rows = {r.headline_id: r for r in store.headlines_since(utc(2026, 1, 1))}
    assert rows[koreaboo[0].headline_id].published_at == utc(2026, 10, 14, 8, 10, 19)
    assert rows[koreaboo[2].headline_id].published_at == FETCHED     # future date replaced by first_seen
    store.close()
