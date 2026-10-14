"""Optional YouTube Data API v3 pass (spec §3.3). Runs only when YOUTUBE_API_KEY is set; RSS stays the base.

With a key, each run after the RSS pass:

1. **Discovery** (`playlistItems.list` on a channel's uploads playlist `UU…`, 1 unit for up to 50 uploads).
   RSS only lists a channel's latest 15 uploads, so this backfills uploads 16-50. It runs for channels whose
   RSS fetch failed this run, for channels with no stored videos yet (cold start), and for a rotating twelfth
   of all channels (so every channel is re-read about every 6 h). New, attributed, ≤30-day-old uploads are kept.
2. **Statistics** (`videos.list`, 1 unit per 50 ids, `part=statistics,contentDetails`): exact view counts for
   (a) tracked videos that are not in this run's RSS (they left the 15-item window), (b) newly discovered
   uploads, (c) RSS videos, whose coarse, cached RSS view count the API value then replaces for this run.
   `contentDetails.duration` classifies discovered uploads that RSS never showed (Shorts detection).

Quota: 1 unit per call, at most `RUN_CAP` units per run and `SP_YOUTUBE_DAILY_UNITS` (default 9,000 of the free
10,000) per Pacific-time day, enforced through the `api_usage` ledger. When the budget runs out the remaining
work is skipped and RSS values stand. The key travels only in the `X-Goog-Api-Key` request header, never in a
URL, so it cannot reach logs, error strings, the DB or trends.json.
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from ..http import HttpError, Response, redact
from ..matching import AliasMatcher, classify_video, normalize
from ..timeutil import pacific_date, parse_iso
from .base import ApiSnapshot, ApiUsage, RunContext, VideoObs

API_BASE = "https://www.googleapis.com/youtube/v3"
VIDEOS_URL = f"{API_BASE}/videos"
PLAYLIST_ITEMS_URL = f"{API_BASE}/playlistItems"
KEY_HEADER = "X-Goog-Api-Key"
BATCH = 50
RUN_CAP = 200              # 48 runs/day x 200 = 9,600 < 10,000 even if the daily ledger were lost
DISCOVERY_SHARE = 0.5      # discovery may spend at most half of a run's units; statistics get the rest
DISCOVERY_SLOTS = 12       # a channel is re-discovered once per 12 slots...
SLOT_SEC = 1800            # ...of 30 minutes = every 6 hours
TRACK_DAYS = 30
SHORTS_MAX_S = 180         # Shorts can be up to 3 minutes long
SHORT_CLIP_S = 60
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
KEY_REASONS = frozenset({
    "forbidden",          # also what a request without a usable key gets ("missing a valid API key")
    "keyInvalid", "keyExpired", "API_KEY_INVALID", "accessNotConfigured", "SERVICE_DISABLED",
    "ipRefererBlocked", "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED",
    "API_KEY_SERVICE_BLOCKED", "API_KEY_ANDROID_APP_BLOCKED", "API_KEY_IOS_APP_BLOCKED",
})
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


# ---------------------------------------------------------------------------- pure parsers


@dataclass(frozen=True)
class VideoStat:
    views: int | None
    duration_s: int | None


@dataclass(frozen=True)
class Upload:
    video_id: str
    channel_id: str
    title: str
    published_at: datetime
    thumbnail_url: str | None


def parse_duration(raw: str | None) -> int | None:
    """ISO-8601 duration (`PT3M12S`, `P0D`, `PT1H2M`) → whole seconds, or None when unparseable."""
    if not raw:
        return None
    m = _DURATION_RE.match(raw.strip())
    if not m or all(g is None for g in m.groups()):
        return None
    d, h, mi, sec = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + int(float(sec or 0))


def parse_video_items(content: bytes) -> dict[str, VideoStat]:
    data = json.loads(content.decode("utf-8"))
    out: dict[str, VideoStat] = {}
    for item in data.get("items", []) or []:
        vid = item.get("id")
        if not isinstance(vid, str):
            continue
        raw = (item.get("statistics") or {}).get("viewCount")
        try:
            views = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            views = None
        out[vid] = VideoStat(views, parse_duration((item.get("contentDetails") or {}).get("duration")))
    return out


def parse_api_stats(content: bytes) -> dict[str, int]:
    """View counts only (kept for callers that need nothing else)."""
    return {vid: s.views for vid, s in parse_video_items(content).items() if s.views is not None}


def parse_playlist_items(content: bytes, channel_id: str) -> list[Upload]:
    """Uploads from a playlistItems.list page. Private/deleted entries (no videoPublishedAt) are skipped."""
    data = json.loads(content.decode("utf-8"))
    out: list[Upload] = []
    for item in data.get("items", []) or []:
        snippet = item.get("snippet") or {}
        details = item.get("contentDetails") or {}
        vid = details.get("videoId") or (snippet.get("resourceId") or {}).get("videoId")
        published_raw = details.get("videoPublishedAt")
        if not isinstance(vid, str) or not _VIDEO_ID_RE.match(vid) or not published_raw:
            continue
        try:
            published = parse_iso(str(published_raw))
        except ValueError:
            continue
        thumbs = snippet.get("thumbnails") or {}
        thumb = next((thumbs[k].get("url") for k in ("high", "medium", "default")
                      if isinstance(thumbs.get(k), dict) and thumbs[k].get("url")), None)
        out.append(Upload(vid, channel_id, " ".join(str(snippet.get("title") or "").split()), published, thumb))
    return out


def api_error_reason(content: bytes) -> str | None:
    """The machine-readable reason of a Google API error body (`errors[].reason` or `details[].reason`)."""
    try:
        err = json.loads(content.decode("utf-8")).get("error") or {}
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if not isinstance(err, dict):
        return None
    for entry in (err.get("details") or []) + (err.get("errors") or []):
        if isinstance(entry, dict) and isinstance(entry.get("reason"), str):
            return entry["reason"][:60]
    status = err.get("status")
    return status[:60] if isinstance(status, str) else None


def uploads_playlist_id(channel_id: str) -> str:
    return "UU" + channel_id[2:]


def discovery_slot(channel_id: str) -> int:
    return zlib.crc32(channel_id.encode("ascii", "replace")) % DISCOVERY_SLOTS


def current_slot(now: datetime) -> int:
    return int(now.timestamp() // SLOT_SEC) % DISCOVERY_SLOTS


def is_api_short(title: str, duration_s: int | None) -> bool | None:
    """Shorts test for uploads RSS never showed (RSS's `/shorts/` link is authoritative when we have it).

    There is no API field for "is a Short", and duration alone is ambiguous: Shorts may run up to 180 s while
    horizontal teasers are often ≤60 s. So: `#shorts` in the title and ≤180 s → Short; ≤60 s and not a teaser
    title → Short; otherwise a regular video. Unknown or zero duration (premieres, live) → None, decide later.
    """
    if duration_s is None or duration_s <= 0:
        return None
    if "#shorts" in normalize(title).casefold():
        return duration_s <= SHORTS_MAX_S
    if duration_s <= SHORT_CLIP_S:
        return classify_video(title, "") != "teaser"
    return False


def video_url(video_id: str, short: bool) -> str:
    # The URL carries the Shorts decision, exactly like an RSS link, so classify_video() stays the single rule.
    return f"https://www.youtube.com/shorts/{video_id}" if short else f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------- the pass


class _Stop(Exception):
    """A fatal API answer (quota exhausted or key rejected): no further calls this run."""


@dataclass
class ApiPass:
    budget: int
    used_before: int
    units: int = 0
    discovered: list[VideoObs] = field(default_factory=list)
    snapshots: list[ApiSnapshot] = field(default_factory=list)
    superseded: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    exhausted: bool = False
    day: date | None = None

    @property
    def ledger_units(self) -> int:
        """Units to record. A quota-exhausted answer books the rest of the day's budget so later runs skip."""
        if self.exhausted:
            return max(self.units, self.budget - self.used_before)
        return self.units

    @property
    def usage(self) -> ApiUsage | None:
        if self.day is None or self.ledger_units <= 0:
            return None
        return ApiUsage("youtube", self.day, self.ledger_units)

    def detail(self) -> str:
        text = f"API {self.units} units ({self.used_before + self.ledger_units}/{self.budget} today)"
        return " · ".join([text, *self.flags])

    def flag(self, text: str) -> None:
        if text not in self.flags:
            self.flags.append(text)


class _Caller:
    def __init__(self, ctx: RunContext, p: ApiPass, limit: int):
        self.ctx, self.p, self.limit = ctx, p, limit
        self.headers = {KEY_HEADER: ctx.config.youtube_api_key or ""}

    def can_spend(self, cap: int) -> bool:
        return self.p.units < min(cap, self.limit)

    def get(self, base: str, params: dict[str, str], label: str) -> Response | None:
        """One API call (1 unit, charged before sending: Google bills failed requests too)."""
        self.p.units += 1
        url = f"{base}?{urlencode(params)}"
        try:
            resp = self.ctx.http.get(url, headers=self.headers)
        except HttpError as exc:
            self.p.errors.append(redact(f"API {label}: {exc}")[:200])
            return None
        if resp.status == 200:
            return resp
        reason = api_error_reason(resp.content) or ""
        self.p.errors.append(f"API {label}: HTTP {resp.status} {reason}".strip()[:200])
        if reason in QUOTA_REASONS:
            self.p.exhausted = True
            self.p.flag("quota exhausted")
            raise _Stop()
        if resp.status == 401 or (resp.status in (400, 403) and reason in KEY_REASONS):
            self.p.flag("key rejected")
            raise _Stop()
        return None


def _attributed(ctx: RunContext, matcher: AliasMatcher, channel_id: str, title: str) -> str | None:
    ch = ctx.seed.channel_by_id.get(channel_id)
    if ch is None:
        return None
    return ch.owner if ch.kind == "artist" else matcher.primary(title)


def discovery_channels(ctx: RunContext, failed: set[str]) -> list[str]:
    """Channels to re-read this run: RSS failures, never-seen channels, then this slot's rotation share."""
    resolved = sorted(ch.channel_id for ch in ctx.seed.resolved_channels() if ch.channel_id)
    slot = current_slot(ctx.now)
    first = [c for c in resolved if c in failed]
    unseen = [c for c in resolved if c not in failed and c not in ctx.store.channels_with_videos]
    rota = [c for c in resolved if c not in failed and c in ctx.store.channels_with_videos
            and discovery_slot(c) == slot]
    return first + unseen + rota


def fetch_api_pass(ctx: RunContext, rss_videos: list[VideoObs], failed_channels: set[str]) -> ApiPass:
    budget = ctx.config.youtube_daily_units
    day = pacific_date(ctx.now)
    used = ctx.store.api_units_used("youtube", day)
    p = ApiPass(budget=budget, used_before=used, day=day)
    limit = min(RUN_CAP, max(0, budget - used))
    if not ctx.config.youtube_api_key:
        return p
    if limit <= 0:
        p.flag("daily budget used")
        return p
    call = _Caller(ctx, p, limit)
    matcher = AliasMatcher(ctx.seed.artists)
    cutoff = ctx.now - timedelta(days=TRACK_DAYS)

    # Candidates from RSS (attributed, non-short, ≤30 d), as in the keyless scoring rules.
    rss_ids = {v.video_id for v in rss_videos}
    rss_published: dict[str, datetime] = {}
    for v in rss_videos:
        if v.published_at < cutoff or classify_video(v.title, v.url) == "short":
            continue
        if _attributed(ctx, matcher, v.channel_id, v.title):
            rss_published[v.video_id] = v.published_at
    with_rss_views = {v.video_id for v in rss_videos if v.views is not None}
    published = {**ctx.store.published_at, **rss_published}
    tracked = (set(ctx.store.api_candidates) | set(rss_published)) - with_rss_views
    upgrade = set(rss_published) & with_rss_views

    uploads: dict[str, Upload] = {}
    try:
        # 1. discovery
        disc_cap = max(1, int(limit * DISCOVERY_SHARE))
        for cid in discovery_channels(ctx, failed_channels):
            if not call.can_spend(disc_cap):
                p.flag("discovery cap")
                break
            if ctx.time_left() <= 0:
                p.errors.append("API: deadline reached during discovery")
                break
            resp = call.get(PLAYLIST_ITEMS_URL, {"part": "snippet,contentDetails",
                                                 "playlistId": uploads_playlist_id(cid), "maxResults": str(BATCH)},
                            f"uploads {cid}")
            if resp is None:
                continue
            try:
                found = parse_playlist_items(resp.content, cid)
            except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                p.errors.append(f"API uploads {cid}: bad JSON {exc}"[:200])
                continue
            for up in found:
                if (up.video_id in rss_ids or up.video_id in ctx.store.known_videos or up.video_id in uploads
                        or not (cutoff <= up.published_at <= ctx.now)
                        or "#shorts" in normalize(up.title).casefold()
                        or not _attributed(ctx, matcher, cid, up.title)):
                    continue
                uploads[up.video_id] = up

        # 2. statistics, most valuable first
        def newest_first(ids, when):
            return sorted(ids, key=lambda i: (-when.get(i, cutoff).timestamp(), i))

        order = newest_first(tracked, published)
        order += newest_first(uploads, {k: u.published_at for k, u in uploads.items()})
        order += newest_first(upgrade, published)
        order = list(dict.fromkeys(order))                    # de-duplicate, keep priority order
        for i in range(0, len(order), BATCH):
            if not call.can_spend(limit):
                p.flag("budget cap")
                break
            if ctx.time_left() <= 0:
                p.errors.append("API: deadline reached during statistics")
                break
            batch = order[i : i + BATCH]
            resp = call.get(VIDEOS_URL, {"part": "statistics,contentDetails", "id": ",".join(batch),
                                         "maxResults": str(BATCH)}, "videos")
            if resp is None:
                continue
            try:
                stats = parse_video_items(resp.content)
            except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                p.errors.append(f"API videos: bad JSON {exc}"[:200])
                continue
            observed = ctx.observed()
            for vid in batch:
                st = stats.get(vid)
                if st is None:
                    continue                                   # deleted or private: no snapshot
                up = uploads.get(vid)
                if up is not None:
                    short = is_api_short(up.title, st.duration_s)
                    if short is None:
                        continue                               # premiere/live: rediscover later
                    p.discovered.append(VideoObs(
                        video_id=vid, channel_id=up.channel_id, title=up.title, url=video_url(vid, short),
                        published_at=up.published_at, views=None, observed_at=observed,
                        thumbnail_url=up.thumbnail_url,
                    ))
                    if short or st.views is None:
                        continue
                elif st.views is None:
                    continue
                p.snapshots.append(ApiSnapshot(vid, observed, st.views))
                if vid in upgrade:
                    p.superseded.add(vid)
    except _Stop:
        pass
    p.snapshots.sort(key=lambda s: s.video_id)
    return p
