"""Build and atomically write trends.json (spec §7). Key order follows the contract exactly."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .fetchers.base import SOURCE_IDS, SOURCE_NAMES, clip_detail
from .scoring import ArtistScore, Mention
from .timeutil import iso

SCHEMA_VERSION = 1
MAX_HEADLINES = 5


@dataclass(frozen=True)
class SourceInfo:
    id: str
    status: str
    last_success_at: datetime | None
    detail: str


def wiki_url(lang: str, title: str | None) -> str | None:
    if not title:
        return None
    return f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_,'")


def _r(x: float | None, nd: int) -> float | None:
    if x is None:
        return None
    v = round(float(x), nd)
    return 0.0 if v == 0 else v  # never emit -0.0


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def item_metrics(s: ArtistScore) -> dict[str, Any]:
    top = s.top
    ko, en = s.wiki_ko, s.wiki_en
    day = s.wiki_day
    return {
        "yt_views_per_hour": int(round(top.display_vph)) if top else None,
        "yt_views_per_hour_estimated": bool(top.estimated) if top else False,
        "yt_accel": _r(s.r_top, 2),
        "wiki_day": day.isoformat() if day else None,
        "wiki_ko_views": int(ko.x) if not ko.missing and ko.x is not None else None,
        "wiki_ko_ratio": _r(ko.ratio, 2) if not ko.missing else None,
        "wiki_en_views": int(en.x) if not en.missing and en.x is not None else None,
        "wiki_en_ratio": _r(en.ratio, 2) if not en.missing else None,
        "headlines_24h": s.news.headlines_24h,
        "headlines_ko_24h": s.news.headlines_ko_24h,
    }


def _headline(m: Mention) -> dict[str, Any]:
    return {
        "title": _clip(m.title, 300),
        "url": m.url,
        "source_id": m.source_id,
        "source_name": SOURCE_NAMES.get(m.source_id, m.source_id),
        "lang": m.lang,
        "published_at": iso(m.published_at),
    }


def build_item(s: ArtistScore, channel_url: str | None) -> dict[str, Any]:
    top = s.top
    top_video = None
    if top is not None:
        v = top.video
        top_video = {
            "video_id": v.video_id,
            "title": _clip(v.title, 200) or v.video_id,
            "url": f"https://www.youtube.com/watch?v={v.video_id}",
            "kind": v.kind,
            "published_at": iso(v.published_at),
            "views": int(top.V_L),
            "channel_name": _clip(v.channel_name or "YouTube", 100),
        }
    return {
        "id": s.id,
        "rank": s.rank,
        "prev_rank": s.prev_rank,
        "change": s.change,
        "change_amount": s.change_amount,
        "name": _clip(s.artist.name, 40),
        "name_ko": _clip(s.artist.name_ko, 40) if s.artist.name_ko else None,
        "score": float(s.score),
        "korea_first": s.korea_first,
        "why": s.why,
        "why_source": s.why_source,
        "image_url": f"https://i.ytimg.com/vi/{top.video.video_id}/hqdefault.jpg" if top else None,
        "metrics": item_metrics(s),
        "signals": {
            "youtube": _r(s.s_yt, 3),
            "accel": _r(s.s_accel, 3),
            "new_release": _r(s.s_new, 3),
            "wiki": _r(s.s_wiki, 3),
            "news": _r(s.news.s_news, 3),
        },
        "top_video": top_video,
        "headlines": [_headline(m) for m in s.news.recent[:MAX_HEADLINES]],
        "links": {
            "youtube_channel": channel_url,
            "wikipedia_en": wiki_url("en", s.artist.wiki_en),
            "wikipedia_ko": wiki_url("ko", s.artist.wiki_ko),
        },
    }


def build_payload(
    *,
    now: datetime,
    interval_sec: int,
    ranked: Sequence[ArtistScore],
    top_n: int,
    rank_basis_at: datetime | None,
    cold_start: bool,
    sources: Mapping[str, SourceInfo],
    channel_urls: Mapping[str, str | None],
) -> dict[str, Any]:
    items = [build_item(s, channel_urls.get(s.id)) for s in ranked[:top_n]]
    src_list = []
    for sid in SOURCE_IDS:
        info = sources.get(sid) or SourceInfo(sid, "error", None, "no result")
        src_list.append({
            "id": sid,
            "name": SOURCE_NAMES[sid],
            "status": info.status,
            "last_success_at": iso(info.last_success_at) if info.last_success_at else None,
            "detail": clip_detail(info.detail),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "next_update_hint": iso(now + timedelta(seconds=interval_sec)),
        "rank_change_basis_at": iso(rank_basis_at) if rank_basis_at else None,
        "cold_start": bool(cold_start),
        "items": items,
        "sources": src_list,
    }


def dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Write `<path>.tmp`, fsync, then os.replace: readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
