"""Optional YouTube Data API statistics pass (spec §3.3). Only runs when YOUTUBE_API_KEY is set."""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlencode

from ..http import HttpError, redact
from ..matching import AliasMatcher, classify_video
from .base import ApiSnapshot, RunContext, VideoObs

API_URL = "https://www.googleapis.com/youtube/v3/videos"
BATCH = 50


def parse_api_stats(content: bytes) -> dict[str, int]:
    data = json.loads(content.decode("utf-8"))
    out: dict[str, int] = {}
    for item in data.get("items", []) or []:
        vid = item.get("id")
        raw = (item.get("statistics") or {}).get("viewCount")
        if isinstance(vid, str) and raw is not None:
            try:
                out[vid] = int(raw)
            except (TypeError, ValueError):
                continue
    return out


def api_candidates(ctx: RunContext, rss_videos: list[VideoObs]) -> list[str]:
    """Non-short, attributed, ≤30 d old videos with no snapshot from this run's RSS pass."""
    with_snapshot = {v.video_id for v in rss_videos if v.views is not None}
    cands = set(ctx.store.api_candidates)
    matcher = AliasMatcher(ctx.seed.artists)
    channels = ctx.seed.channel_by_id
    cutoff = ctx.now - timedelta(days=30)
    for v in rss_videos:
        if v.published_at < cutoff or classify_video(v.title, v.url) == "short":
            continue
        ch = channels.get(v.channel_id)
        if ch is None:
            continue
        artist = ch.owner if ch.kind == "artist" else matcher.primary(v.title)
        if artist:
            cands.add(v.video_id)
    return sorted(cands - with_snapshot)


def fetch_api_snapshots(ctx: RunContext, rss_videos: list[VideoObs]) -> tuple[list[ApiSnapshot], int, list[str]]:
    key = ctx.config.youtube_api_key
    if not key:
        return [], 0, []
    ids = api_candidates(ctx, rss_videos)
    snaps: list[ApiSnapshot] = []
    errors: list[str] = []
    calls = 0
    for i in range(0, len(ids), BATCH):
        if ctx.time_left() <= 0:
            errors.append("API: deadline reached")
            break
        batch = ids[i : i + BATCH]
        url = f"{API_URL}?{urlencode({'part': 'statistics', 'id': ','.join(batch), 'key': key})}"
        calls += 1
        try:
            resp = ctx.http.get(url)
        except HttpError as exc:
            errors.append(redact(f"API: {exc}"))
            continue
        if resp.status != 200:
            errors.append(f"API: HTTP {resp.status}")
            continue
        try:
            stats = parse_api_stats(resp.content)
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(f"API: bad JSON {exc}"[:200])
            continue
        observed = ctx.observed()
        snaps.extend(ApiSnapshot(vid, observed, views) for vid, views in sorted(stats.items()))
    return snaps, calls, errors
