"""Optional YouTube Data API v3 pass (spec §3.3). Runs only when YOUTUBE_API_KEY is set; RSS stays the base.

With a key, each run after the RSS pass:

1. **Discovery** (`playlistItems.list` on a channel's uploads playlist `UU…`, 1 unit for up to 50 uploads).
   RSS only lists a channel's latest 15 uploads, so this backfills uploads 16-50. It runs for channels whose
   RSS fetch failed this run, for channels with no stored videos yet (cold start), and for a rotating twelfth
   of all channels (so every channel is re-read about every 6 h). New, attributed, ≤30-day-old uploads are kept.
   When a channel's playlist answers `playlistNotFound`, `channels.list` (1 unit per 50 channels) tells why:
   no public videos → "empty" (a seed error; logged as a warning, skipped for 7 days); a different uploads
   playlist → read and stored ("resolved"); otherwise the long-form playlist `UULF…` is tried ("uulf"), and if
   that is missing too the channel is "API-blind": while its RSS fails it is discovered through `search.list`
   (100 units), at most once per 6 h per channel and once per run, and only when the day's budget can still
   carry every remaining run's regular work.
2. **Statistics** (`videos.list`, 1 unit per 50 ids, `part=statistics,contentDetails`): exact view counts for
   (a) tracked videos that are not in this run's RSS (they left the 15-item window), (b) newly discovered
   uploads, (c) RSS videos, whose coarse, cached RSS view count the API value then replaces for this run.
   `contentDetails.duration` classifies discovered uploads that RSS never showed (Shorts detection).

Quota: every call is booked before it is sent; regular calls are capped at `RUN_CAP` units per run, plus at most
one `search.list` (`SEARCH_COST`), and `SP_YOUTUBE_DAILY_UNITS` (default 9,000 of the free 10,000) per
Pacific-time day, enforced through the `api_usage` ledger. When the budget runs out the remaining work is
skipped and RSS values stand. The key travels only in the `X-Goog-Api-Key` request header, never in a URL, so
it cannot reach logs, error strings, the DB or trends.json.
"""

from __future__ import annotations

import html
import json
import re
import zlib
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode

from ..http import HttpError, Response, redact
from ..matching import AliasMatcher, classify_video, normalize
from ..timeutil import PACIFIC, iso, pacific_date, parse_iso
from .base import ApiSnapshot, ApiUsage, RunContext, UploadsState, VideoObs

API_BASE = "https://www.googleapis.com/youtube/v3"
VIDEOS_URL = f"{API_BASE}/videos"
PLAYLIST_ITEMS_URL = f"{API_BASE}/playlistItems"
CHANNELS_URL = f"{API_BASE}/channels"
SEARCH_URL = f"{API_BASE}/search"
KEY_HEADER = "X-Goog-Api-Key"
BATCH = 50
RUN_CAP = 200              # daily ledger (9,000) is the real guard; if it were lost, Google's quotaExceeded stops the day
DISCOVERY_SHARE = 0.5      # discovery may spend at most half of a run's units; statistics get the rest
DISCOVERY_SLOTS = 12       # a channel is re-discovered once per 12 slots...
SLOT_SEC = 1800            # ...of 30 minutes = every 6 hours
TRACK_DAYS = 30
UNAVAILABLE_DAYS = 7       # an empty or API-blind channel keeps that method this long, then is probed again
SEARCH_COST = 100          # search.list is billed 100 units per call (every other call here costs 1)
SEARCH_EVERY = timedelta(hours=6)   # an API-blind channel is searched at most this often...
SEARCH_PER_RUN = 1         # ...and at most one search per run, on top of RUN_CAP
PACE_SEC = 1200            # the headroom check assumes a run every 20 min until the quota day ends (ticker: 30)
METHOD_RESOLVED = "resolved"   # channels.list reported a different uploads playlist
METHOD_UULF = "uulf"           # long-form uploads playlist UULF… (no Shorts, no live streams)
METHOD_SEARCH = "search"       # no readable playlist: search.list while RSS fails
METHOD_EMPTY = "empty"         # no public videos at all: nothing to discover (seed points at the wrong channel?)
BLOCKING_METHODS = frozenset({METHOD_SEARCH, METHOD_EMPTY})
SHORTS_MAX_S = 180         # Shorts can be up to 3 minutes long
SHORT_CLIP_S = 60
NOT_FOUND_REASONS = frozenset({"playlistNotFound"})
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


def _thumbnail(snippet: dict) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    return next((thumbs[k].get("url") for k in ("high", "medium", "default")
                 if isinstance(thumbs.get(k), dict) and thumbs[k].get("url")), None)


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
        out.append(Upload(vid, channel_id, " ".join(str(snippet.get("title") or "").split()), published,
                          _thumbnail(snippet)))
    return out


def parse_search_items(content: bytes, channel_id: str) -> list[Upload]:
    """Videos from a search.list(part=snippet, type=video) page.

    Search snippets carry HTML-escaped titles (`&#39;`, `&amp;`), so they are unescaped here. Live and upcoming
    broadcasts are skipped: they have no duration yet, and a later search finds them once they are VODs. Items of
    another channel are ignored (defensive; `channelId=` already filters)."""
    data = json.loads(content.decode("utf-8"))
    out: list[Upload] = []
    for item in data.get("items", []) or []:
        ident = item.get("id")
        vid = ident.get("videoId") if isinstance(ident, dict) else None
        snippet = item.get("snippet") or {}
        if not isinstance(vid, str) or not _VIDEO_ID_RE.match(vid):
            continue
        if snippet.get("channelId") not in (None, channel_id):
            continue
        if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
            continue
        try:
            published = parse_iso(str(snippet.get("publishedAt") or ""))
        except ValueError:
            continue
        title = " ".join(html.unescape(str(snippet.get("title") or "")).split())
        out.append(Upload(vid, channel_id, title, published, _thumbnail(snippet)))
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


def long_form_playlist_id(channel_id: str) -> str:
    """The channel's long-form uploads playlist (its Videos tab: no Shorts, no live streams)."""
    return "UULF" + channel_id[2:]


@dataclass(frozen=True)
class ChannelInfo:
    uploads: str | None        # contentDetails.relatedPlaylists.uploads
    video_count: int | None    # statistics.videoCount (public videos); None when not reported


def parse_channels(content: bytes) -> dict[str, ChannelInfo]:
    """channels.list(part=contentDetails,statistics) → {channel_id: ChannelInfo} for every channel returned."""
    data = json.loads(content.decode("utf-8"))
    out: dict[str, ChannelInfo] = {}
    for item in data.get("items", []) or []:
        cid = item.get("id")
        if not isinstance(cid, str):
            continue
        uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        raw = (item.get("statistics") or {}).get("videoCount")
        try:
            count = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            count = None
        out[cid] = ChannelInfo(uploads if isinstance(uploads, str) and uploads else None, count)
    return out


def parse_channel_uploads(content: bytes) -> dict[str, str]:
    """channels.list → {channel_id: uploads playlist id} for channels that report one."""
    return {cid: info.uploads for cid, info in parse_channels(content).items() if info.uploads}


def search_params(channel_id: str, now: datetime) -> dict[str, str]:
    """search.list for a channel's newest videos of the tracking window (100 units however many come back)."""
    return {"part": "snippet", "channelId": channel_id, "type": "video", "order": "date",
            "publishedAfter": iso(now - timedelta(days=TRACK_DAYS)), "maxResults": str(BATCH)}


def discovery_slot(channel_id: str) -> int:
    return zlib.crc32(channel_id.encode("ascii", "replace")) % DISCOVERY_SLOTS


def current_slot(now: datetime) -> int:
    return int(now.timestamp() // SLOT_SEC) % DISCOVERY_SLOTS


def runs_left_today(now: datetime) -> int:
    """Runs still to come in this YouTube quota day (it ends at midnight Pacific), one per PACE_SEC."""
    local = now.astimezone(PACIFIC)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=PACIFIC)
    left = (midnight.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    return max(0, int(left // PACE_SEC))


def blocks_playlists(state: UploadsState | None, now: datetime) -> bool:
    """True while a stored method says no playlist works for the channel ("search" or "empty")."""
    return (state is not None and state.playlist_id is None and state.method in BLOCKING_METHODS
            and state.unavailable_until is not None and state.unavailable_until > now)


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
    search_units: int = 0                               # the part of `units` spent on search.list
    discovered: list[VideoObs] = field(default_factory=list)
    snapshots: list[ApiSnapshot] = field(default_factory=list)
    superseded: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    uploads_state: list[UploadsState] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)      # channels whose uploads were discovered this run
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
    def __init__(self, ctx: RunContext, p: ApiPass, limit: int, remaining: int):
        self.ctx, self.p = ctx, p
        self.limit = limit             # regular calls this run: min(RUN_CAP, remaining)
        self.remaining = remaining     # the day's budget left before this run
        self.headers = {KEY_HEADER: ctx.config.youtube_api_key or ""}

    def can_spend(self, cap: int) -> bool:
        """Room for one more regular (1-unit) call under `cap`; search units do not count against RUN_CAP."""
        regular = self.p.units - self.p.search_units
        return regular < min(cap, self.limit) and self.p.units < self.remaining

    def get(self, base: str, params: dict[str, str], label: str,
            soft_404: frozenset[str] = frozenset(), *, search: bool = False) -> Response | None:
        """One API call, charged before sending (Google bills failed requests too): 1 unit, or SEARCH_COST.

        A 404 whose reason is in `soft_404` is an expected answer: the response is returned (status 404) without
        recording an error, so the caller decides what it means."""
        cost = SEARCH_COST if search else 1
        self.p.units += cost
        if search:
            self.p.search_units += cost
        url = f"{base}?{urlencode(params)}"
        try:
            resp = self.ctx.http.get(url, headers=self.headers)
        except HttpError as exc:
            self.p.errors.append(redact(f"API {label}: {exc}")[:200])
            return None
        if resp.status == 200:
            return resp
        reason = api_error_reason(resp.content) or ""
        if resp.status == 404 and reason in soft_404:
            return resp
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


def unavailable_channels(ctx: RunContext) -> set[str]:
    """Channels playlist discovery skips right now ("search": search.list instead; "empty": nothing to find)."""
    return {cid for cid, s in ctx.store.uploads_state.items() if blocks_playlists(s, ctx.now)}


def discovery_channels(ctx: RunContext, failed: set[str]) -> list[str]:
    """Channels to re-read this run: RSS failures, never-seen channels, then this slot's rotation share.

    RSS failures come in rotation order starting at this run's slot, so when the discovery cap cannot take
    them all (a long global RSS outage on a bigger roster) a different part is cut each run, never the same one.
    """
    skip = unavailable_channels(ctx)
    resolved = sorted(ch.channel_id for ch in ctx.seed.resolved_channels() if ch.channel_id and ch.channel_id not in skip)
    slot = current_slot(ctx.now)
    first = sorted((c for c in resolved if c in failed),
                   key=lambda c: ((discovery_slot(c) - slot) % DISCOVERY_SLOTS, c))
    unseen = [c for c in resolved if c not in failed and c not in ctx.store.channels_with_videos]
    rota = [c for c in resolved if c not in failed and c in ctx.store.channels_with_videos
            and discovery_slot(c) == slot]
    return first + unseen + rota


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def fetch_api_pass(ctx: RunContext, rss_videos: list[VideoObs], failed_channels: set[str]) -> ApiPass:
    budget = ctx.config.youtube_daily_units
    day = pacific_date(ctx.now)
    used = ctx.store.api_units_used("youtube", day)
    p = ApiPass(budget=budget, used_before=used, day=day)
    remaining = max(0, budget - used)
    limit = min(RUN_CAP, remaining)
    if not ctx.config.youtube_api_key:
        return p
    if limit <= 0:
        p.flag("daily budget used")
        return p
    call = _Caller(ctx, p, limit, remaining)
    matcher = AliasMatcher(ctx.seed.artists)
    cutoff = ctx.now - timedelta(days=TRACK_DAYS)
    roster = {ch.channel_id for ch in ctx.seed.resolved_channels() if ch.channel_id}

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
    states: dict[str, UploadsState] = {}                  # this run's changes per channel; the last one wins
    tried: dict[str, set[str]] = {}                       # playlists read (or found missing) this run

    def state_of(cid: str) -> UploadsState | None:
        return states.get(cid) or ctx.store.uploads_state.get(cid)

    try:
        def keep(cid: str, found: list[Upload]) -> None:
            """New, attributed, in-window, non-#shorts uploads go on to statistics (and the Shorts rule)."""
            for up in found:
                if (up.video_id in rss_ids or up.video_id in ctx.store.known_videos or up.video_id in uploads
                        or not (cutoff <= up.published_at <= ctx.now)
                        or "#shorts" in normalize(up.title).casefold()
                        or not _attributed(ctx, matcher, cid, up.title)):
                    continue
                uploads[up.video_id] = up

        def discover(cid: str, playlist: str) -> str:
            """Read one uploads playlist. Returns "ok", "missing" (playlistNotFound) or "failed"."""
            tried.setdefault(cid, set()).add(playlist)
            resp = call.get(PLAYLIST_ITEMS_URL, {"part": "snippet,contentDetails", "playlistId": playlist,
                                                 "maxResults": str(BATCH)}, f"uploads {cid}", NOT_FOUND_REASONS)
            if resp is None:
                return "failed"
            if resp.status != 200:
                return "missing"
            try:
                found = parse_playlist_items(resp.content, cid)
            except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                p.errors.append(f"API uploads {cid}: bad JSON {exc}"[:200])
                return "failed"
            p.covered.add(cid)
            keep(cid, found)
            return "ok"

        def mark(cid: str, method: str, why: str) -> None:
            until = ctx.now + timedelta(days=UNAVAILABLE_DAYS)
            prev = state_of(cid)
            searched = prev.searched_at if prev is not None and prev.method == METHOD_SEARCH else None
            states[cid] = UploadsState(cid, None, until, ctx.now, method, searched)
            if method == METHOD_EMPTY:
                ctx.log.warning("youtube API: channel %s has no public videos (%s): the seed most likely points at "
                                "the wrong channel; API discovery skips it until %s", cid, why, until.date())
            else:
                ctx.log.info("youtube API: no readable uploads playlist for %s (%s); discovered through search.list "
                             "while its RSS fails, until %s", cid, why, until.date())

        # 1. discovery through the uploads playlist (derived UU…, or the stored resolved/UULF playlist)
        disc_cap = max(1, int(limit * DISCOVERY_SHARE))
        pending: list[str] = []                               # playlist answered playlistNotFound: ask channels.list
        for cid in discovery_channels(ctx, failed_channels):
            if not call.can_spend(disc_cap):
                p.flag("discovery cap")
                break
            if ctx.time_left() <= 0:
                p.errors.append("API: deadline reached during discovery")
                break
            state = ctx.store.uploads_state.get(cid)
            stored = state.playlist_id if state else None
            if discover(cid, stored or uploads_playlist_id(cid)) == "missing":
                pending.append(cid)

        # 1b. why is it missing? channels.list (1 unit per 50 channels) → resolved playlist, UULF, search or empty
        for i in range(0, len(pending), BATCH):
            if not call.can_spend(disc_cap):
                p.flag("discovery cap")
                break
            if ctx.time_left() <= 0:
                p.errors.append("API: deadline reached during discovery")
                break
            chunk = pending[i : i + BATCH]
            resp = call.get(CHANNELS_URL, {"part": "contentDetails,statistics", "id": ",".join(chunk)}, "channels")
            if resp is None:
                continue
            try:
                info = parse_channels(resp.content)
            except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                p.errors.append(f"API channels: bad JSON {exc}"[:200])
                continue
            for cid in chunk:
                ci = info.get(cid)
                if ci is None or ci.video_count == 0:
                    mark(cid, METHOD_EMPTY, "not returned by channels.list" if ci is None else "videoCount 0")
                    continue
                outcome = "missing"
                for playlist in (ci.uploads, long_form_playlist_id(cid)):
                    if not playlist or playlist in tried.get(cid, set()):
                        continue
                    if not call.can_spend(disc_cap):
                        p.flag("discovery cap")
                        outcome = "deferred"                  # probe again next run; no state change
                        break
                    outcome = discover(cid, playlist)
                    if outcome == "ok":
                        if playlist != uploads_playlist_id(cid):
                            method = METHOD_UULF if playlist == long_form_playlist_id(cid) else METHOD_RESOLVED
                            states[cid] = UploadsState(cid, playlist, None, ctx.now, method)   # cached for later runs
                            ctx.log.info("youtube API: %s uploads read from %s (%s)", cid, playlist, method)
                        elif state_of(cid) is not None:
                            states[cid] = UploadsState(cid, None, None, ctx.now)   # the derived id works again
                        break
                    if outcome != "missing":
                        break                                 # transport/HTTP error: try again next run
                if outcome == "missing":
                    mark(cid, METHOD_SEARCH, "uploads and long-form playlists not found")

        # 1c. API-blind channels whose RSS failed this run: search.list, throttled and only with budget headroom
        def headroom() -> bool:
            planned = len(tracked | set(uploads) | upgrade)
            stats_calls = -(-(planned + BATCH) // BATCH)      # ceil, plus one batch for what the search may find
            regular = p.units - p.search_units + stats_calls
            if regular > limit or p.units + SEARCH_COST + stats_calls > remaining:
                return False
            # Every remaining run of this quota day must still afford this run's regular work.
            return used + p.units + SEARCH_COST + stats_calls + runs_left_today(ctx.now) * regular <= budget

        def due(cid: str) -> bool:
            st = state_of(cid)
            return (cid in failed_channels and blocks_playlists(st, ctx.now) and st is not None
                    and st.method == METHOD_SEARCH
                    and (st.searched_at is None or ctx.now - st.searched_at >= SEARCH_EVERY))

        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        blind = sorted((c for c in roster if due(c)),
                       key=lambda c: (getattr(state_of(c), "searched_at", None) or epoch, c))
        for cid in blind[:SEARCH_PER_RUN]:
            if ctx.time_left() <= 0:
                p.errors.append("API: deadline reached during search")
                break
            if not headroom():
                p.flag("search deferred")
                break
            prev = state_of(cid)
            if prev is None:                                  # unreachable: due() requires a stored method
                continue
            states[cid] = replace(prev, searched_at=ctx.now)  # stamped even if the call fails: never retried hot
            resp = call.get(SEARCH_URL, search_params(cid, ctx.now), f"search {cid}", search=True)
            if resp is None:
                continue
            try:
                found = parse_search_items(resp.content, cid)
            except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                p.errors.append(f"API search {cid}: bad JSON {exc}"[:200])
                continue
            p.covered.add(cid)
            keep(cid, found)

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
    p.uploads_state = [states[c] for c in sorted(states)]
    blocked = [st.method for st in map(state_of, sorted(roster)) if st is not None and blocks_playlists(st, ctx.now)]
    for method, label in ((METHOD_SEARCH, "via search"), (METHOD_EMPTY, "empty")):
        if n := blocked.count(method):
            p.flag(f"{_plural(n, 'channel')} {label}")
    return p
