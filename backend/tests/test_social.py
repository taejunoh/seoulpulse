from __future__ import annotations

import json

import pytest

from seoulpulse.cli import main
from seoulpulse.social import NothingToPost, build_threads, build_x, render_drafts, x_weight

from .conftest import EXAMPLE_PATH

GOLDEN_X = """Korea-first K-pop TOP 5 · Oct 14

1. KISS OF LIFE NEW 🇰🇷 — "Lucky Seven" views up 4.0x vs. normal decay
2. aespa ▲2 🇰🇷 — New MV "Neon Tide" at 127K views/hr
3. Lim Young-woong 🇰🇷
4. SEVENTEEN ▼2
5. LE SSERAFIM ▲2

🇰🇷 = Korea moved first
#kpop #SeoulRising"""

GOLDEN_THREADS = """Korea-first K-pop Trends — TOP 5 (Oct 14, 18:00 KST)

1. KISS OF LIFE NEW 🇰🇷
   "Lucky Seven" views up 4.0x vs. normal decay
2. aespa ▲2 🇰🇷
   New MV "Neon Tide" at 127K views/hr
3. Lim Young-woong 🇰🇷
   2 Korean headlines, no English coverage yet
4. SEVENTEEN ▼2
   6 headlines in 24h (1 Korean)
5. LE SSERAFIM ▲2
   English Wikipedia views 2.1x the 2-week avg

🇰🇷 = Korea moved first · fan-made, not affiliated"""


@pytest.fixture
def example():
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_x_golden(example):
    x = build_x(example)
    assert x == GOLDEN_X
    assert x_weight(x) == 266
    assert x_weight(x) <= 280


def test_threads_golden(example):
    t = build_threads(example)
    assert t == GOLDEN_THREADS
    assert len(t) == 412
    assert len(t) <= 500


def test_render_headers(example):
    out = render_drafts(example)
    assert out.startswith("=== X (266/280) ===\n" + GOLDEN_X + "\n\n=== Threads (412/500) ===\n")
    assert out.endswith(GOLDEN_THREADS + "\n")


def test_x_weight_rules():
    assert x_weight("abc") == 3
    assert x_weight("—") == 1            # U+2014 is in 2010–201F
    assert x_weight("▲") == 2
    assert x_weight("🇰🇷") == 4            # two regional indicators
    assert x_weight("에스파") == 6


def test_limits_hold_for_long_whys(example):
    for it in example["items"]:
        it["why"] = "W" * 60
        it["name_ko"] = "가" * 20
    assert x_weight(build_x(example, top=10)) <= 280
    assert len(build_threads(example, top=5)) <= 500


def test_empty_items_nothing_to_post(example, tmp_path, capsys):
    example["items"] = []
    with pytest.raises(NothingToPost):
        build_x(example)
    p = tmp_path / "empty.json"
    p.write_text(json.dumps(example), encoding="utf-8")
    assert main(["social", "--input", str(p)]) == 1
    assert "nothing to post" in capsys.readouterr().out


def test_cli_social_prints_both(capsys):
    assert main(["social", "--input", str(EXAMPLE_PATH), "--format", "both"]) == 0
    out = capsys.readouterr().out
    assert "=== X (266/280) ===" in out and "=== Threads (412/500) ===" in out
    assert "[Sample]" not in out           # no headlines ever appear
