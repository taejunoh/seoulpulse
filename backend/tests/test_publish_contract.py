from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace

import jsonschema
import pytest

from seoulpulse import publish
from seoulpulse.pipeline import run_once

from .conftest import EXAMPLE_PATH, SCHEMA_PATH, FakeClock, utc
from .fakenet import FakeNet

NOW = utc(2026, 10, 14, 9, 0, 0)
SENTINELS = ("BODY-SENTINEL", "CONTENT-SENTINEL", "FIXTURE-SECRET-DESCRIPTION", "본문은")


@pytest.fixture(scope="module")
def schema():
    s = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(s)
    return s


def run(config, now=NOW, **kw):
    clock = FakeClock()
    return run_once(config, now=now, transport=FakeNet().transport, sleep=clock.sleep, monotonic=clock,
                    rand=lambda: 0.0, deadline_sec=10**6, **kw)


def test_example_validates(schema):
    jsonschema.validate(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")), schema)


def test_generated_payload_validates_and_ranks_are_contiguous(config, schema):
    assert run(config).exit_code == 0
    text = config.trends_path.read_text(encoding="utf-8")
    payload = json.loads(text)
    jsonschema.validate(payload, schema)
    ranks = [it["rank"] for it in payload["items"]]
    assert ranks == list(range(1, len(ranks) + 1))
    assert list(payload) == ["schema_version", "generated_at", "next_update_hint", "rank_change_basis_at",
                             "cold_start", "items", "sources"]
    assert payload["generated_at"] == "2026-10-14T09:00:00Z"
    assert payload["next_update_hint"] == "2026-10-14T09:30:00Z"
    # UTF-8, ensure_ascii=False, 2-space indent, trailing newline
    assert "에스파" in text or "르세라핌" in text or any(it.get("name_ko") for it in payload["items"])
    assert "\\u" not in text
    assert text.startswith('{\n  "schema_version": 1,')
    assert text.endswith("}\n")
    for s in SENTINELS:
        assert s not in text


def test_headlines_are_untranslated_and_limited(config):
    run(config)
    payload = json.loads(config.trends_path.read_text(encoding="utf-8"))
    nj = next((it for it in payload["items"] if it["id"] == "newjeans"), None)
    assert nj is not None
    assert nj["headlines"][0]["title"] == "[픽스처] 뉴진스가 샘플 신곡으로 컴백…'테스트 패턴' 공개"
    assert nj["headlines"][0]["source_name"] == "Yonhap" and nj["headlines"][0]["lang"] == "ko"
    for it in payload["items"]:
        assert len(it["headlines"]) <= 5
        times = [h["published_at"] for h in it["headlines"]]
        assert times == sorted(times, reverse=True)


def test_no_stored_row_contains_article_text(config):
    run(config)
    import sqlite3

    conn = sqlite3.connect(config.db_path)
    dump = "\n".join(conn.iterdump())
    conn.close()
    for s in SENTINELS:
        assert s not in dump


def test_same_db_and_now_is_byte_identical(config, tmp_path):
    run(config)
    # Two copies of the same DB state; re-score without fetching using the same --now.
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        (d / "var").mkdir(parents=True)
        shutil.copy(config.db_path, d / "var" / "seoulpulse.db")
    later = utc(2026, 10, 14, 9, 30, 0)
    outs = []
    for d in (a, b):
        cfg = replace(config, db_path=d / "var" / "seoulpulse.db", public_dir=d / "public")
        clock = FakeClock()
        run_once(cfg, now=later, fetchers=[], sleep=clock.sleep, monotonic=clock)
        outs.append((d / "public" / "trends.json").read_bytes())
    assert outs[0] == outs[1]


def test_atomic_write_never_partially_writes_destination(tmp_path, monkeypatch):
    dest = tmp_path / "trends.json"
    dest.write_text("OLD\n", encoding="utf-8")
    seen = {}

    def failing_replace(src, dst):
        seen["src"] = os.fspath(src)
        raise OSError("disk full")

    monkeypatch.setattr(publish.os, "replace", failing_replace)
    with pytest.raises(OSError):
        publish.write_atomic(dest, '{"new": true}\n')
    assert dest.read_text(encoding="utf-8") == "OLD\n"
    assert seen["src"].endswith("trends.json.tmp")
    monkeypatch.undo()
    publish.write_atomic(dest, '{"new": true}\n')
    assert dest.read_text(encoding="utf-8") == '{"new": true}\n'
    assert not (tmp_path / "trends.json.tmp").exists()


def test_wiki_link_encoding():
    assert publish.wiki_url("en", "Kiss of Life (group)") == "https://en.wikipedia.org/wiki/Kiss_of_Life_(group)"
    assert publish.wiki_url("ko", "세븐틴 (음악 그룹)") == (
        "https://ko.wikipedia.org/wiki/%EC%84%B8%EB%B8%90%ED%8B%B4_(%EC%9D%8C%EC%95%85_%EA%B7%B8%EB%A3%B9)")
    assert publish.wiki_url("ko", None) is None


def test_zero_items_keeps_existing_file(config):
    config.public_dir.mkdir(parents=True)
    config.trends_path.write_text('{"old": true}\n', encoding="utf-8")
    clock = FakeClock()
    # Only Koreaboo, whose fixture mentions EXO/IVE once each in English: scores stay below 3.0? Use no sources.
    out = run_once(config, now=NOW, fetchers=[], sources=[], sleep=clock.sleep, monotonic=clock)
    assert out.items == 0
    assert config.trends_path.read_text(encoding="utf-8") == '{"old": true}\n'
