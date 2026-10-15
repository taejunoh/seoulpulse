"""YouTube channel RSS (spec §3.3). Keyless; the optional Data API pass lives in youtube_api.py."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

from defusedxml import ElementTree as ET

from ..http import HttpError
from ..timeutil import parse_iso
from .base import (
    ApiSnapshot,
    ApiUsage,
    CacheEntry,
    FetchOutput,
    RunContext,
    UploadsState,
    VideoObs,
    make_result,
    multi_unit_status,
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class FeedFormatError(ValueError):
    """The body parsed as XML but is not an Atom feed (e.g. a consent page served with HTTP 200)."""


def _clean_xml(xml: bytes) -> bytes:
    # Some servers prepend a BOM or whitespace before the XML declaration.
    return xml.lstrip(b"\xef\xbb\xbf \t\r\n")


def parse_youtube_feed(xml: bytes, observed_at: datetime, *, channel_id: str | None = None) -> list[VideoObs]:
    """Parse a channel feed. The feed-level yt:channelId lacks the UC prefix and is ignored."""
    root = ET.fromstring(_clean_xml(xml))
    if root.tag != f"{{{NS['atom']}}}feed":
        raise FeedFormatError(f"not an Atom feed (root element <{root.tag}>)")
    out: list[VideoObs] = []
    for entry in root.findall("atom:entry", NS):
        vid = (entry.findtext("yt:videoId", default="", namespaces=NS) or "").strip()
        if not VIDEO_ID_RE.match(vid):
            continue
        cid = (entry.findtext("yt:channelId", default="", namespaces=NS) or "").strip()
        if not cid.startswith("UC"):
            cid = channel_id or (f"UC{cid}" if cid else "")
        title = (entry.findtext("atom:title", default="", namespaces=NS) or "").strip()
        url = ""
        for link in entry.findall("atom:link", NS):
            if link.get("rel", "alternate") == "alternate" and link.get("href"):
                url = link.get("href", "")
                break
        if not url:
            url = f"https://www.youtube.com/watch?v={vid}"
        published_raw = entry.findtext("atom:published", default="", namespaces=NS)
        try:
            published = parse_iso(published_raw)
        except ValueError:
            continue
        views: int | None = None
        stats = entry.find("media:group/media:community/media:statistics", NS)
        if stats is not None:
            try:
                views = int(stats.get("views", ""))
            except ValueError:
                views = None
        thumb = entry.find("media:group/media:thumbnail", NS)
        out.append(VideoObs(
            video_id=vid, channel_id=cid, title=title, url=url, published_at=published,
            views=views, observed_at=observed_at,
            thumbnail_url=thumb.get("url") if thumb is not None else None,
        ))
    return out


class YouTubeFetcher:
    source_id = "youtube"

    def fetch(self, ctx: RunContext) -> FetchOutput:
        started = ctx.observed()
        channels = ctx.seed.resolved_channels()
        total = len(channels)
        ok = 0
        errors: list[str] = []
        videos: list[VideoObs] = []
        cache: list[CacheEntry] = []
        failed: set[str] = set()
        for i, ch in enumerate(channels):
            if ctx.time_left() <= 0:
                errors.append(f"deadline reached; {total - i} channels skipped")
                break
            url = FEED_URL.format(ch.channel_id)
            try:
                resp = ctx.http.get(url, validators=ctx.store.http_validators(url))
            except HttpError as exc:
                errors.append(f"{ch.handle}: {exc}")
                failed.add(ch.channel_id)
                continue
            if resp.status == 304:
                ok += 1
                continue
            if resp.status != 200:
                errors.append(f"{ch.handle}: HTTP {resp.status}")
                failed.add(ch.channel_id)
                continue
            try:
                obs = parse_youtube_feed(resp.content, ctx.observed(), channel_id=ch.channel_id)
            except Exception as exc:  # noqa: BLE001 - malformed feed is a unit error
                errors.append(f"{ch.handle}: parse error {type(exc).__name__}: {exc}"[:200])
                failed.add(ch.channel_id)
                continue
            videos.extend(obs)
            ok += 1
            if resp.etag or resp.last_modified:
                cache.append(CacheEntry(url, resp.etag, resp.last_modified, ctx.observed()))

        detail = f"RSS {ok}/{total} channels"
        units_ok = ok
        api_snaps: list[ApiSnapshot] = []
        usage: list[ApiUsage] = []
        uploads_state: list[UploadsState] = []
        if ctx.config.youtube_api_key:
            from .youtube_api import fetch_api_pass

            api = fetch_api_pass(ctx, videos, failed)
            # A channel whose RSS feed failed but whose uploads playlist the API read this run is covered: its
            # new uploads were discovered and its tracked videos got exact counts. So an RSS outage that the API
            # bridges reads `degraded`/`ok` instead of `error` (spec §3.3 Status).
            bridged = len(api.covered & failed)
            if bridged:
                units_ok += bridged
                detail = f"RSS {ok}/{total} + API {bridged} channels"
            if api.superseded:
                # The exact API count replaces the coarse RSS count for this run (one snapshot, not two).
                videos = [replace(v, views=None) if v.video_id in api.superseded else v for v in videos]
            videos.extend(api.discovered)
            api_snaps = api.snapshots
            usage = [api.usage] if api.usage else []
            uploads_state = api.uploads_state
            # Keep the bridged form inside the 80-character detail: "RSS 2/77 + API 71 channels · 84 units (…)".
            detail += " · " + (api.detail().removeprefix("API ") if bridged else api.detail())
            errors.extend(api.errors)
        result = make_result(self.source_id, multi_unit_status(units_ok, total), started, ctx.observed(),
                             units_ok, total, detail, errors)
        return FetchOutput(result=result, videos=videos, api_snapshots=api_snaps, http_cache=cache,
                           api_usage=usage, uploads_state=uploads_state)
