from __future__ import annotations

import sys
import types
from dataclasses import replace

import pytest

from seoulpulse import why as why_mod
from seoulpulse.config import load_config
from seoulpulse.scoring import ArtistInfo, score_artist
from seoulpulse.store import Store
from seoulpulse.why import accept_llm_text, apply_llm_whys, llm_enabled

from .conftest import utc

NOW = utc(2026, 10, 14, 9, 0, 0)


def scores(n=3):
    out = []
    for i in range(n):
        s = score_artist(ArtistInfo(f"a{i}", f"Artist {i}"), [], {}, [], NOW, None)
        s.why = "Buzzing across Korean sources"
        out.append(s)
    return out


def test_disabled_by_default_and_never_imports_anthropic(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    cfg = load_config({}, warn=False)
    assert cfg.why_llm is False and cfg.llm_model == "claude-haiku-5-5"
    assert not llm_enabled(cfg)
    st = Store(tmp_path / "db.sqlite")
    ranked = scores()
    assert apply_llm_whys(ranked, {}, cfg, st, NOW) == 0
    assert all(s.why_source == "template" for s in ranked)
    assert "anthropic" not in sys.modules
    st.close()


def test_enabled_flag_without_key_or_package_stays_off(monkeypatch):
    cfg = load_config({"SP_WHY_LLM": "1"}, warn=False)
    assert not llm_enabled(cfg)                                  # no API key
    cfg2 = load_config({"SP_WHY_LLM": "1", "ANTHROPIC_API_KEY": "test-key"}, warn=False)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert not llm_enabled(cfg2)                                 # package not importable


@pytest.mark.parametrize(("text", "ok"), [
    ("Lucky Seven views 4x above normal decay", True),
    ("  padded line  ", True),
    ("", False),
    ("x" * 61, False),
    ("two\nlines", False),
    ("에스파 is trending", False),                               # Hangul rejected
    (None, False),
])
def test_accept_rules(text, ok):
    assert (accept_llm_text(text) is not None) is ok


def test_llm_results_cached_and_failures_fall_back(tmp_path):
    cfg = replace(load_config({"SP_WHY_LLM": "1", "ANTHROPIC_API_KEY": "k"}, warn=False))
    st = Store(tmp_path / "db.sqlite")
    calls = []

    def fake_call(config, payload):
        calls.append(payload)
        assert "headlines" not in payload and "template_line" in payload
        if payload["artist"] == "Artist 1":
            raise RuntimeError("network down")
        return f"{payload['artist']} up on fresh numbers"

    ranked = scores()
    assert apply_llm_whys(ranked, {}, cfg, st, NOW, call=fake_call) == 3
    assert [s.why_source for s in ranked] == ["llm", "template", "llm"]
    assert ranked[1].why == "Buzzing across Korean sources"

    again = scores()
    calls.clear()
    apply_llm_whys(again, {}, cfg, st, NOW, call=fake_call)
    assert [p["artist"] for p in calls] == ["Artist 1"]          # others served from why_cache
    assert again[0].why == "Artist 0 up on fresh numbers" and again[0].why_source == "llm"
    st.close()


def test_call_shape_with_fake_sdk(monkeypatch):
    captured = {}

    class Block:
        def __init__(self, type_, text=""):
            self.type, self.text = type_, text

    class Resp:
        stop_reason = "end_turn"
        content = [Block("thinking"), Block("text", "Short English line")]

    class Messages:
        def create(self, **kw):
            captured.update(kw)
            return Resp()

    class Client:
        def __init__(self, **kw):
            captured["client"] = kw
            self.beta = types.SimpleNamespace(messages=Messages())

    fake = types.ModuleType("anthropic")
    fake.Anthropic = Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    cfg = load_config({"SP_WHY_LLM": "1", "ANTHROPIC_API_KEY": "k"}, warn=False)
    assert why_mod._call_llm(cfg, {"artist": "X"}) == "Short English line"
    assert captured["client"] == {"timeout": 20, "max_retries": 1}
    assert captured["model"] == "claude-haiku-5-5"
    assert captured["max_tokens"] == 1024
    assert captured["output_config"] == {"effort": "low"}
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["extra_body"] == {"fallbacks": "default"}
    assert captured["messages"][0]["role"] == "user"

    Resp.stop_reason = "refusal"
    assert why_mod._call_llm(cfg, {"artist": "X"}) is None
