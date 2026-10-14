"""The one-line "why" (spec §6): deterministic templates, plus an optional LLM rewrite (off by default).

`anthropic` is imported lazily and only when SP_WHY_LLM=1 and ANTHROPIC_API_KEY are both set.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .matching import extract_song
from .scoring import ArtistScore, VideoCalc, age_now

if TYPE_CHECKING:
    from .config import Config
    from .store import Store

log = logging.getLogger("seoulpulse.why")

WHY_MAX = 60
FALLBACK = "Buzzing across Korean sources"
LLM_TOP = 10
LLM_MAX_CALLS = 10
LLM_CACHE_TTL = timedelta(hours=6)
LLM_SYSTEM = (
    "Write ONE English line ≤60 chars explaining why this K-pop act is trending, using only the given numbers. "
    "No quotes from headlines, no hashtags, no emoji."
)
_HANGUL_RE = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")


def _strip_point_zero(s: str) -> str:
    return s[:-2] if s.endswith(".0") else s


def fmt(n: float) -> str:
    """Compact number: 999 → "999", 7584 → "7.6K", 126667 → "127K", 1250000 → "1.2M"."""
    n = max(0.0, float(n))
    if round(n) < 1000:
        return str(int(round(n)))
    if n < 10_000:
        s = f"{n / 1000:.1f}"
        if float(s) < 10:
            return _strip_point_zero(s) + "K"
    if n < 1_000_000:
        k = round(n / 1000)
        if k < 1000:
            return f"{k}K"
    return _strip_point_zero(f"{n / 1_000_000:.1f}") + "M"


def _fits(text: str | None) -> bool:
    return text is not None and 1 <= len(text) <= WHY_MAX


def _render(song: str | None, with_song: Callable[[str], str], no_song: str) -> str | None:
    if song:
        t = with_song(song)
        if _fits(t):
            return t
    return no_song if _fits(no_song) else None


def _newest(calcs: Sequence[VideoCalc], kind: str, now: datetime, max_age: float) -> VideoCalc | None:
    found = [c for c in calcs if c.video.kind == kind and age_now(c.video, now) <= max_age]
    return max(found, key=lambda c: (c.video.published_at, c.video.video_id)) if found else None


def template_why(s: ArtistScore, now: datetime) -> tuple[str, str]:
    """Return (why, rule_id) using the first rule (R1..R9) whose condition holds and whose text fits."""
    # R1 NEW_MV
    mv = _newest(s.calcs, "mv", now, 48)
    if mv is not None:
        v = fmt(mv.display_vph)
        t = _render(extract_song(mv.video.title), lambda song: f'New MV "{song}" at {v} views/hr',
                    f"New MV out, {v} views/hr")
        if t:
            return t, "R1"
    # R2 ACCEL
    if s.top is not None and s.r_top is not None and s.r_top >= 2.0 and s.s_yt >= 0.2:
        r = f"{s.r_top:.1f}"
        noun = "MV" if s.top.video.kind == "mv" else "Video"
        t = _render(extract_song(s.top.video.title), lambda song: f'"{song}" views up {r}x vs. normal decay',
                    f"{noun} views up {r}x vs. normal decay")
        if t:
            return t, "R2"
    # R3 KO_WIKI
    if s.ko_lead_wiki and s.wiki_ko.ratio is not None:
        t = f"Korean Wikipedia views {s.wiki_ko.ratio:.1f}x normal, EN quiet"
        if _fits(t):
            return t, "R3"
    # R4 KO_NEWS
    if s.ko_lead_news:
        n_ko = s.news.headlines_ko_24h
        if s.news.ko_no_en:
            t = f"{n_ko} Korean headline{'s' if n_ko != 1 else ''}, no English coverage yet"
        else:
            t = f"Korean press led English by {s.news.ko_lead_hours}h"
        if _fits(t):
            return t, "R4"
    # R5 WIKI
    zk = s.wiki_ko.z if not s.wiki_ko.missing else None
    ze = s.wiki_en.z if not s.wiki_en.missing else None
    best = max((z for z in (zk, ze) if z is not None), default=None)
    if best is not None and best >= 2.0:
        if zk is not None and zk >= (ze if ze is not None else float("-inf")):
            label, ratio = "Korean", s.wiki_ko.ratio
        else:
            label, ratio = "English", s.wiki_en.ratio
        t = f"{label} Wikipedia views {ratio:.1f}x the 2-week avg"
        if _fits(t):
            return t, "R5"
    # R6 NEWS
    if s.news.headlines_24h >= 2:
        n, n_ko = s.news.headlines_24h, s.news.headlines_ko_24h
        t = f"{n} headlines in 24h ({n_ko} Korean)" if n_ko else f"{n} headlines in 24h"
        if _fits(t):
            return t, "R6"
    # R7 TEASER
    teasers = [v for v in s.candidates if v.kind == "teaser" and age_now(v, now) <= 72]
    if teasers:
        tv = max(teasers, key=lambda v: (v.published_at, v.video_id))
        t = _render(extract_song(tv.title), lambda song: f'New teaser "{song}": comeback incoming',
                    "New teaser out: comeback incoming")
        if t:
            return t, "R7"
    # R8 MOMENTUM
    if s.top is not None:
        v = fmt(s.top.display_vph)
        t = _render(extract_song(s.top.video.title), lambda song: f'"{song}" steady at {v} views/hr',
                    f"Steady YouTube pull: {v} views/hr")
        if t:
            return t, "R8"
    return FALLBACK, "R9"


# ---------------------------------------------------------------------------- optional LLM


def llm_enabled(config: Config) -> bool:
    if not (config.why_llm and config.anthropic_api_key):
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("anthropic") is not None
    except (ImportError, ValueError):
        return False


def llm_input(s: ArtistScore, template: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """Metrics and signals only. Headline titles are never sent."""
    return {
        "artist": s.artist.name,
        "template_line": template,
        "korea_first": s.korea_first,
        "metrics": metrics,
        "signals": {"youtube": round(s.s_yt, 3), "accel": round(s.s_accel, 3), "new_release": round(s.s_new, 3),
                    "wiki": round(s.s_wiki, 3), "news": round(s.news.s_news, 3)},
        "top_video_kind": s.top.video.kind if s.top else None,
    }


def accept_llm_text(text: str | None) -> str | None:
    if text is None:
        return None
    t = text.strip()
    if "\n" in t or "\r" in t or not _fits(t) or _HANGUL_RE.search(t):
        return None
    return t


def _call_llm(config: Config, payload: dict[str, Any]) -> str | None:
    import anthropic  # lazy: only reached when the feature is enabled

    client = anthropic.Anthropic(timeout=20, max_retries=1)
    resp = client.beta.messages.create(
        model=config.llm_model,
        max_tokens=1024,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        system=LLM_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        return None
    parts = [getattr(b, "text", "") for b in getattr(resp, "content", []) or [] if getattr(b, "type", "") == "text"]
    return accept_llm_text("".join(parts))


def apply_llm_whys(
    ranked: Sequence[ArtistScore],
    metrics_by_id: dict[str, dict[str, Any]],
    config: Config,
    store: Store,
    now: datetime,
    call: Callable[[Config, dict[str, Any]], str | None] | None = None,
) -> int:
    """Rewrite the top-10 whys with the LLM when enabled. Returns the number of API calls made."""
    if call is None:
        if not llm_enabled(config):
            return 0
        call = _call_llm
    calls = 0
    for s in ranked[:LLM_TOP]:
        payload = llm_input(s, s.why, metrics_by_id.get(s.id, {}))
        key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        cached = store.why_cache_get(key, now - LLM_CACHE_TTL)
        if cached is not None:
            s.why, s.why_source = cached, "llm"
            continue
        if calls >= LLM_MAX_CALLS:
            break
        calls += 1
        try:
            text = call(config, payload)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the template
            log.warning("LLM why failed for %s: %s", s.id, type(exc).__name__)
            continue
        if text:
            s.why, s.why_source = text, "llm"
            store.why_cache_put(key, text, now)
    return calls
