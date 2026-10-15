"""Scoring (spec §5): pure functions of plain rows + `now`. No I/O here."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .timeutil import hours, iso, kst_date

if TYPE_CHECKING:
    from .fetchers.base import SearchRow

# ---------------------------------------------------------------------------- constants (§5.1)
P = 0.8
T0 = 2.0
REF = 24.0
MIN_GAP = timedelta(minutes=25)
VEL_WIN = timedelta(hours=6)
KIND_W = {"mv": 1.0, "perf": 0.6, "teaser": 0.35, "show": 0.25, "other": 0.15}
TRACK_DAYS = 30
STALE_H = 6.0
DROP_H = 72.0
STALE_HALF = 24.0
SRC_W = {"yonhap": 1.5, "soompi": 1.0, "koreaboo": 0.8}
NEWS_HALF = 12.0
NEWS_WIN = 48.0
K_BASE = 0.676
KO_LEAD_H = 6.0
KO_EN_LOOKBACK_H = 24.0  # E = English mentions published at or after t_ko − 24 h (§5.4)
W_YT, W_ACCEL, W_NEW, W_WIKI, W_NEWS = 0.35, 0.15, 0.15, 0.20, 0.15
KF_BONUS = 6.0
MIN_SCORE = 3.0
# Korean search spike (Naver DataLab, §5.3). Used only when the source is configured; otherwise every term
# below is absent and all scores are exactly the keyless ones.
W_SEARCH = 0.10          # additive: 100·W_SEARCH·S_search on top of the five keyless terms
SEARCH_Z_FULL = 6.0      # S_search = clamp(z_search / 6, 0, 1)
SEARCH_SIGMA_FLOOR = 0.25  # σ_eff = max(σ, 0.25·μ): a 2x day can score at most z = 4
SEARCH_MIN_MU = 0.01     # baseline mean in Naver's batch-relative units; below it rounding noise dominates
SEARCH_STALE_D = 2       # D must be ≥ target − 2 days (target = yesterday in KST)
SEARCH_KF_Z = 2.0        # ko_lead_search: z_search ≥ 2 while English Wikipedia is quiet (z_en < 1 or missing)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


# ---------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Snapshot:
    observed_at: datetime
    views: int
    source: str = "rss"     # "rss" (coarse, cached feed count) | "api" (exact Data API count); never paired across


@dataclass(frozen=True)
class Video:
    video_id: str
    artist_id: str | None
    kind: str
    title: str
    published_at: datetime
    snapshots: tuple[Snapshot, ...] = ()
    url: str = ""
    channel_id: str = ""
    channel_name: str = ""


@dataclass(frozen=True)
class Mention:
    artist_id: str
    headline_id: str
    source_id: str
    lang: str
    title: str
    url: str
    published_at: datetime


@dataclass(frozen=True)
class ArtistInfo:
    id: str
    name: str
    name_ko: str | None = None
    wiki_en: str | None = None
    wiki_ko: str | None = None


# ---------------------------------------------------------------------------- per-video (§5.2)


@dataclass(frozen=True)
class VideoCalc:
    video: Video
    t_L: datetime
    V_L: int
    age_L: float
    c_life: float
    has_pair: bool
    t_O: datetime | None
    V_O: int | None
    vph: float | None
    age_mid: float | None
    c_recent: float | None
    c: float
    I_raw: float
    I: float
    r: float | None
    display_vph: float
    estimated: bool
    stale_h: float
    e: float
    t_P: datetime | None = None          # newer end of the velocity pair (t_L unless the pair fell back, §5.2)
    pair_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video.video_id, "kind": self.video.kind, "published_at": iso(self.video.published_at),
            "t_L": iso(self.t_L), "V_L": self.V_L, "age_L": self.age_L, "c_life": self.c_life,
            "pair": self.has_pair, "t_O": iso(self.t_O) if self.t_O else None, "V_O": self.V_O,
            "t_P": iso(self.t_P) if self.t_P else None, "pair_source": self.pair_source, "vph": self.vph,
            "age_mid": self.age_mid, "c_recent": self.c_recent, "c": self.c, "I_raw": self.I_raw, "I": self.I,
            "r": self.r, "display_vph": self.display_vph, "estimated": self.estimated, "stale_h": self.stale_h,
            "e": self.e,
        }


def is_candidate(v: Video, now: datetime) -> bool:
    return v.kind != "short" and v.kind in KIND_W and v.artist_id is not None \
        and v.published_at >= now - timedelta(days=TRACK_DAYS)


def velocity_pair(snaps: Sequence[Snapshot]) -> tuple[Snapshot, Snapshot] | None:
    """The (older, newer) snapshots for the recent velocity (§5.2), never mixing count sources.

    `snaps` is sorted by time. The pair lies in [t_L−6h, t_L], spans ≥25 min and must grow (`V_newer > V_older`).
    RSS counts are cached and coarse while API counts are exact, so a pair across sources would measure the gap
    between the two counters, not views. The latest snapshot's source is tried first (with one source this is
    exactly the single-series rule); if it has no valid pair, e.g. on the first run after the API takes over or
    falls back to RSS, the other source's own newest snapshot in the window ends the pair.
    """
    if not snaps:
        return None
    lo = snaps[-1].observed_at - VEL_WIN
    by_source: dict[str, list[Snapshot]] = {}
    for s in snaps:
        by_source.setdefault(s.source, []).append(s)
    for series in sorted(by_source.values(), key=lambda ss: ss[-1].observed_at, reverse=True):
        end = series[-1]
        window = [s for s in series if lo <= s.observed_at <= end.observed_at - MIN_GAP]
        if window and end.views > window[0].views:
            return window[0], end
    return None


def calc_video(v: Video, now: datetime) -> VideoCalc | None:
    """Intensity for one eligible video, or None if it is not eligible."""
    if not is_candidate(v, now) or not v.snapshots:
        return None
    snaps = sorted(v.snapshots, key=lambda s: s.observed_at)
    last = snaps[-1]
    t_L, V_L = last.observed_at, last.views
    stale_h = hours(now - t_L)
    if stale_h > DROP_H:
        return None
    age_L = max(0.25, hours(t_L - v.published_at))
    c_life = V_L * (1 - P) / ((age_L + T0) ** (1 - P) - T0 ** (1 - P))

    found = velocity_pair(snaps)
    pair, end = found if found is not None else (None, None)

    vph = age_mid = c_recent = None
    if pair is not None and end is not None:
        span = hours(end.observed_at - pair.observed_at)
        vph = (end.views - pair.views) / span
        mid = pair.observed_at + (end.observed_at - pair.observed_at) / 2
        age_mid = max(0.0, hours(mid - v.published_at))
        c_recent = vph * (age_mid + T0) ** P
    c = c_recent if c_recent is not None else c_life
    I_raw = c * (REF + T0) ** (-P)

    r = None
    if c_recent is not None and V_L >= 1000 and c_life > 0:
        r = clamp(c_recent / c_life, 0.25, 4.0)

    if vph is not None:
        display, estimated = vph, False
    else:
        display, estimated = c_life * (age_L + T0) ** (-P), True

    I = I_raw
    if stale_h > STALE_H:
        I = I_raw * 2 ** (-stale_h / STALE_HALF)
        r = None
    e = KIND_W[v.kind] * I
    return VideoCalc(
        video=v, t_L=t_L, V_L=V_L, age_L=age_L, c_life=c_life, has_pair=pair is not None,
        t_O=pair.observed_at if pair else None, V_O=pair.views if pair else None, vph=vph, age_mid=age_mid,
        c_recent=c_recent, c=c, I_raw=I_raw, I=I, r=r, display_vph=display, estimated=estimated,
        stale_h=stale_h, e=e, t_P=end.observed_at if end else None, pair_source=end.source if end else None,
    )


# ---------------------------------------------------------------------------- per-artist signals (§5.3)


def youtube_signal(calcs: Sequence[VideoCalc]) -> tuple[float, float, VideoCalc | None]:
    ordered = sorted(calcs, key=lambda c: (-c.e, c.video.video_id))
    es = [c.e for c in ordered[:3]] + [0.0, 0.0, 0.0]
    yt_raw = es[0] + 0.5 * es[1] + 0.25 * es[2]
    s_yt = clamp((math.log10(1 + yt_raw) - 3) / 2.5, 0.0, 1.0)
    return yt_raw, s_yt, (ordered[0] if ordered else None)


def accel_signal(top: VideoCalc | None, s_yt: float) -> tuple[float | None, float, float]:
    r_top = top.r if top is not None else None
    s_accel = clamp(math.log2(r_top) / 2, -0.5, 1.0) if r_top is not None else 0.0
    g = clamp(s_yt / 0.4, 0.0, 1.0)
    return r_top, s_accel, g


def age_now(v: Video, now: datetime) -> float:
    return max(0.0, hours(now - v.published_at))


def new_release_signal(candidates: Iterable[Video], now: datetime) -> float:
    best = 0.0
    for v in candidates:
        a = age_now(v, now)
        if v.kind == "mv":
            best = max(best, 1 - a / 48)
        elif v.kind == "teaser":
            best = max(best, 0.4 * (1 - a / 72))
    return clamp(best, 0.0, 1.0)


@dataclass(frozen=True)
class WikiLang:
    lang: str
    title: str | None
    missing: bool
    reason: str = ""
    day: date | None = None
    x: int | None = None
    mu: float | None = None
    sigma: float | None = None
    sigma_eff: float | None = None
    z_raw: float | None = None
    z: float | None = None
    ratio: float | None = None
    baseline_n: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lang": self.lang, "title": self.title, "missing": self.missing, "reason": self.reason,
            "day": self.day.isoformat() if self.day else None, "x": self.x, "mu": self.mu, "sigma": self.sigma,
            "sigma_eff": self.sigma_eff, "z_raw": self.z_raw, "z": self.z, "ratio": self.ratio,
            "baseline_n": self.baseline_n,
        }


def wiki_lang_signal(lang: str, title: str | None, series: Mapping[date, int] | None, today: date) -> WikiLang:
    if not title:
        return WikiLang(lang, title, True, "no title")
    if not series:
        return WikiLang(lang, title, True, "no data")
    D = max(series)
    if D < today - timedelta(days=2):
        return WikiLang(lang, title, True, "stale", day=D)
    base = [series[D - timedelta(days=k)] for k in range(1, 15) if (D - timedelta(days=k)) in series]
    if len(base) < 7:
        return WikiLang(lang, title, True, "short baseline", day=D, baseline_n=len(base))
    mu = statistics.fmean(base)
    sigma = statistics.pstdev(base)
    sigma_eff = max(sigma, 0.25 * mu, 10.0)
    x = series[D]
    z_raw = clamp((x - mu) / sigma_eff, -3.0, 10.0)
    z = z_raw * min(1.0, x / 200) if z_raw > 0 else z_raw
    return WikiLang(lang, title, False, "", day=D, x=x, mu=mu, sigma=sigma, sigma_eff=sigma_eff,
                    z_raw=z_raw, z=z, ratio=x / max(mu, 1.0), baseline_n=len(base))


def wiki_signal(ko: WikiLang, en: WikiLang) -> tuple[float, float]:
    zk = max(ko.z, 0.0) if not ko.missing and ko.z is not None else 0.0
    ze = max(en.z, 0.0) if not en.missing and en.z is not None else 0.0
    wiki_z = 0.6 * zk + 0.4 * ze
    return wiki_z, clamp(wiki_z / 8, 0.0, 1.0)


@dataclass(frozen=True)
class SearchSpike:
    """Spike of one Naver keyword group's daily series. Computed at fetch time; only day/z/ratio are stored."""

    missing: bool
    reason: str = ""
    day: date | None = None
    x: float | None = None
    mu: float | None = None
    sigma: float | None = None
    sigma_eff: float | None = None
    z: float | None = None
    ratio: float | None = None
    baseline_n: int = 0


def search_spike(series: Mapping[date, float], target: date) -> SearchSpike:
    """z-score of the latest day D vs the 14 days before it (spec §5.3, like the wiki signal).

    `series` is one keyword group's `ratio` per KST day from a single DataLab response. Naver scales every
    response so its maximum is 100, but z and x/μ are scale-free, so they compare across runs and batches.
    """
    if not series:
        return SearchSpike(True, "no data")
    D = max(series)
    if D < target - timedelta(days=SEARCH_STALE_D):
        return SearchSpike(True, "stale", day=D)
    base = [series[D - timedelta(days=k)] for k in range(1, 15) if (D - timedelta(days=k)) in series]
    if len(base) < 7:
        return SearchSpike(True, "short baseline", day=D, baseline_n=len(base))
    mu = statistics.fmean(base)
    if mu < SEARCH_MIN_MU:
        return SearchSpike(True, "low volume", day=D, baseline_n=len(base))
    sigma = statistics.pstdev(base)
    sigma_eff = max(sigma, SEARCH_SIGMA_FLOOR * mu)
    x = float(series[D])
    z = clamp((x - mu) / sigma_eff, -3.0, 10.0)
    return SearchSpike(False, "", day=D, x=x, mu=mu, sigma=sigma, sigma_eff=sigma_eff, z=z, ratio=x / mu,
                       baseline_n=len(base))


@dataclass(frozen=True)
class SearchSignal:
    """The scoring-time view of a stored SearchRow (derived values only)."""

    missing: bool
    reason: str = ""
    day: date | None = None
    z: float | None = None
    ratio: float | None = None
    s_search: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"missing": self.missing, "reason": self.reason, "day": self.day.isoformat() if self.day else None,
                "z": self.z, "ratio": self.ratio, "S_search": self.s_search}


def search_signal(row: SearchRow | None, now: datetime) -> SearchSignal:
    if row is None:
        return SearchSignal(True, "no data")
    if row.day is None or row.z is None:
        return SearchSignal(True, row.reason or "no data", day=row.day)
    target = kst_date(now) - timedelta(days=1)
    if row.day < target - timedelta(days=SEARCH_STALE_D):
        return SearchSignal(True, "stale", day=row.day)
    return SearchSignal(False, "", day=row.day, z=row.z, ratio=row.ratio,
                        s_search=clamp(max(row.z, 0.0) / SEARCH_Z_FULL, 0.0, 1.0))


def ko_lead_search(search: SearchSignal | None, en: WikiLang) -> bool:
    if search is None or search.missing or search.z is None or search.z < SEARCH_KF_Z:
        return False
    return en.missing or en.z is None or en.z < 1.0


@dataclass(frozen=True)
class NewsCalc:
    raw: float
    hist_days: int
    base_daily: float
    excess: float
    s_news: float
    headlines_24h: int
    headlines_ko_24h: int
    recent: tuple[Mention, ...]          # age ≤ 48 h, newest first
    ko_lead: bool
    ko_no_en: bool
    ko_lead_hours: int | None
    t_ko: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw, "hist_days": self.hist_days, "base_daily": self.base_daily, "excess": self.excess,
            "s_news": self.s_news, "headlines_24h": self.headlines_24h, "headlines_ko_24h": self.headlines_ko_24h,
            "ko_lead_news": self.ko_lead, "ko_no_en": self.ko_no_en, "ko_lead_hours": self.ko_lead_hours,
            "t_ko": iso(self.t_ko) if self.t_ko else None,
            "mentions_48h": [{"source_id": m.source_id, "lang": m.lang, "published_at": iso(m.published_at)}
                             for m in self.recent],
        }


def mention_age(m: Mention, now: datetime) -> float:
    return max(0.0, hours(now - m.published_at))


def news_signal(mentions: Sequence[Mention], now: datetime, first_run_at: datetime | None,
                en_coverage_from: datetime | None = None) -> NewsCalc:
    """`en_coverage_from`: the earliest English headline we hold (None = unrestricted). The Korean-lead test only
    uses Korean mentions whose 24 h English lookback lies inside that coverage (§5.4)."""
    raw = 0.0
    for m in mentions:
        a = mention_age(m, now)
        if a <= NEWS_WIN:
            raw += SRC_W.get(m.source_id, 0.0) * 2 ** (-a / NEWS_HALF)

    hist_days = 0
    if first_run_at is not None:
        hist_days = min(7, (now - timedelta(hours=24) - first_run_at) // timedelta(days=1))
    base_daily = 0.0
    if hist_days >= 2:
        hi = now - timedelta(hours=24)
        lo = hi - timedelta(days=hist_days)
        base_daily = sum(SRC_W.get(m.source_id, 0.0) for m in mentions if lo <= m.published_at < hi) / hist_days
    excess = max(0.0, raw - K_BASE * base_daily)
    s_news = clamp(math.log2(1 + excess) / 4, 0.0, 1.0)

    h24 = [m for m in mentions if mention_age(m, now) <= 24]
    recent = tuple(sorted((m for m in mentions if mention_age(m, now) <= NEWS_WIN),
                          key=lambda m: (m.published_at, m.headline_id), reverse=True))

    # Korea-first via news (§5.4)
    ko_lead = no_en = False
    lead_h: int | None = None
    t_ko: datetime | None = None
    # "No English mention since t_ko − 24 h" is only evidence if we held English headlines over that span. Feed
    # backlogs differ (Yonhap ~3 days, Soompi ~2, Koreaboo ~half a day), so right after a cold start older
    # Korean mentions would otherwise "lead" English coverage we simply never saw.
    lookback = timedelta(hours=KO_EN_LOOKBACK_H)
    K = [m for m in mentions if m.lang == "ko" and mention_age(m, now) <= NEWS_WIN
         and (en_coverage_from is None or m.published_at >= en_coverage_from + lookback)]
    if any(mention_age(m, now) <= 24 for m in K):
        t_ko = min(m.published_at for m in K)
        E = [m for m in mentions if m.lang == "en" and m.published_at >= t_ko - lookback]
        if not E:
            ko_lead = no_en = True
        else:
            first_en = min(m.published_at for m in E)
            if first_en >= t_ko + timedelta(hours=KO_LEAD_H):
                ko_lead = True
                lead_h = int(hours(first_en - t_ko))
    return NewsCalc(
        raw=raw, hist_days=int(hist_days), base_daily=base_daily, excess=excess, s_news=s_news,
        headlines_24h=len(h24), headlines_ko_24h=sum(1 for m in h24 if m.lang == "ko"), recent=recent,
        ko_lead=ko_lead, ko_no_en=no_en, ko_lead_hours=lead_h, t_ko=t_ko,
    )


def ko_lead_wiki(ko: WikiLang, en: WikiLang) -> bool:
    if ko.missing or ko.z is None or ko.z < 2.0:
        return False
    return en.missing or en.z is None or en.z < 1.0


# ---------------------------------------------------------------------------- per-artist score (§5.5)


@dataclass
class ArtistScore:
    artist: ArtistInfo
    calcs: list[VideoCalc]
    candidates: list[Video]
    top: VideoCalc | None
    yt_raw: float
    s_yt: float
    r_top: float | None
    s_accel: float
    g: float
    s_new: float
    wiki_ko: WikiLang
    wiki_en: WikiLang
    wiki_z: float
    s_wiki: float
    news: NewsCalc
    ko_lead_news: bool
    ko_lead_wiki: bool
    korea_first: bool
    raw_score: float
    score: float
    eligible: bool
    search: SearchSignal | None = None      # None when the Naver source is not configured
    s_search: float = 0.0
    ko_lead_search: bool = False
    rank: int | None = None
    prev_rank: int | None = None
    change: str = "new"
    change_amount: int = 0
    why: str = ""
    why_source: str = "template"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.artist.id

    @property
    def s_kf(self) -> float:
        return 1.0 if self.korea_first else 0.0

    @property
    def korea_first_keyless(self) -> bool:
        """korea_first without the Naver-derived lead (what the optional LLM may be told, spec §6)."""
        return self.ko_lead_news or self.ko_lead_wiki

    @property
    def wiki_day(self) -> date | None:
        days = [w.day for w in (self.wiki_ko, self.wiki_en) if not w.missing and w.day]
        return max(days) if days else None

    def components(self) -> dict[str, Any]:
        return {
            "yt_raw": self.yt_raw, "S_yt": self.s_yt, "r_top": self.r_top, "S_accel": self.s_accel, "g": self.g,
            "S_new": self.s_new, "wiki_z": self.wiki_z, "S_wiki": self.s_wiki, "S_news": self.news.s_news,
            "S_kf": self.s_kf, "ko_lead_news": self.ko_lead_news, "ko_lead_wiki": self.ko_lead_wiki,
            "raw_score": self.raw_score, "score": self.score, "eligible": self.eligible,
            "top_video": self.top.video.video_id if self.top else None,
            "videos": [c.as_dict() for c in self.calcs],
            "wiki": {"ko": self.wiki_ko.as_dict(), "en": self.wiki_en.as_dict()},
            "news": self.news.as_dict(),
            "why": self.why, "why_source": self.why_source,
            **({"S_search": self.s_search, "ko_lead_search": self.ko_lead_search,
                "search": self.search.as_dict()} if self.search is not None else {}),
        }


def combine(s_yt: float, s_accel: float, g: float, s_new: float, s_wiki: float, s_news: float,
            korea_first: bool, s_search: float = 0.0) -> float:
    # With s_search = 0 the extra term adds exactly 0.0, so keyless scores are bit-identical.
    return clamp(
        100 * (W_YT * s_yt + W_ACCEL * s_accel * g + W_NEW * s_new * (0.5 + 0.5 * g)
               + W_WIKI * s_wiki + W_NEWS * s_news + W_SEARCH * s_search)
        + KF_BONUS * (1.0 if korea_first else 0.0),
        0.0, 100.0,
    )


def score_artist(
    artist: ArtistInfo,
    videos: Iterable[Video],
    wiki: Mapping[tuple[str, str], Mapping[date, int]],
    mentions: Sequence[Mention],
    now: datetime,
    first_run_at: datetime | None,
    search: SearchSignal | None = None,
    en_coverage_from: datetime | None = None,
) -> ArtistScore:
    candidates = sorted((v for v in videos if is_candidate(v, now)), key=lambda v: v.video_id)
    calcs = [c for c in (calc_video(v, now) for v in candidates) if c is not None]
    calcs.sort(key=lambda c: (-c.e, c.video.video_id))
    yt_raw, s_yt, top = youtube_signal(calcs)
    r_top, s_accel, g = accel_signal(top, s_yt)
    s_new = new_release_signal(candidates, now)

    today = now.date()
    ko = wiki_lang_signal("ko", artist.wiki_ko, wiki.get(("ko", artist.wiki_ko)) if artist.wiki_ko else None, today)
    en = wiki_lang_signal("en", artist.wiki_en, wiki.get(("en", artist.wiki_en)) if artist.wiki_en else None, today)
    wiki_z, s_wiki = wiki_signal(ko, en)

    news = news_signal(mentions, now, first_run_at, en_coverage_from)
    klw = ko_lead_wiki(ko, en)
    s_search = search.s_search if search is not None and not search.missing else 0.0
    kls = ko_lead_search(search, en)
    kf = news.ko_lead or klw or kls
    raw_score = combine(s_yt, s_accel, g, s_new, s_wiki, news.s_news, kf, s_search)
    score = round(raw_score, 1)
    eligible = score >= MIN_SCORE and max(s_yt, s_wiki, news.s_news, s_search) > 0
    return ArtistScore(
        artist=artist, calcs=calcs, candidates=candidates, top=top, yt_raw=yt_raw, s_yt=s_yt, r_top=r_top,
        s_accel=s_accel, g=g, s_new=s_new, wiki_ko=ko, wiki_en=en, wiki_z=wiki_z, s_wiki=s_wiki, news=news,
        ko_lead_news=news.ko_lead, ko_lead_wiki=klw, korea_first=kf, raw_score=raw_score, score=score,
        eligible=eligible, search=search, s_search=s_search, ko_lead_search=kls,
    )


def score_all(
    artists: Iterable[ArtistInfo],
    videos_by_artist: Mapping[str, Sequence[Video]],
    wiki: Mapping[tuple[str, str], Mapping[date, int]],
    mentions_by_artist: Mapping[str, Sequence[Mention]],
    now: datetime,
    first_run_at: datetime | None,
    search_by_artist: Mapping[str, SearchSignal] | None = None,
    en_coverage_from: datetime | None = None,
) -> list[ArtistScore]:
    """`search_by_artist` is None when the Naver source is not configured (no search term at all).
    `en_coverage_from` is the earliest stored English headline (see `news_signal`); None = unrestricted."""
    def search_for(aid: str) -> SearchSignal | None:
        if search_by_artist is None:
            return None
        return search_by_artist.get(aid) or SearchSignal(True, "no data")

    return [
        score_artist(a, videos_by_artist.get(a.id, ()), wiki, mentions_by_artist.get(a.id, ()), now, first_run_at,
                     search_for(a.id), en_coverage_from)
        for a in artists
    ]


# ---------------------------------------------------------------------------- ranking (§5.5, §5.6, §5.7)


def rank_scores(scores: Iterable[ArtistScore]) -> list[ArtistScore]:
    """Eligible artists sorted by (−score, −yt_raw, id) with ranks 1..n assigned."""
    ranked = sorted((s for s in scores if s.eligible), key=lambda s: (-s.score, -s.yt_raw, s.id))
    for i, s in enumerate(ranked, start=1):
        s.rank = i
    return ranked


def basis_ranks(payload: Mapping[str, Any] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if not payload:
        return out
    for item in payload.get("items", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("rank"), int):
            out[item["id"]] = item["rank"]
    return out


def apply_rank_changes(ranked: Iterable[ArtistScore], basis: Mapping[str, int] | None) -> None:
    basis = basis or {}
    for s in ranked:
        prev = basis.get(s.id)
        s.prev_rank = prev
        if prev is None or s.rank is None:
            s.change, s.change_amount = "new", 0
        elif prev > s.rank:
            s.change, s.change_amount = "up", prev - s.rank
        elif prev < s.rank:
            s.change, s.change_amount = "down", s.rank - prev
        else:
            s.change, s.change_amount = "same", 0


def is_cold_start(scores: Iterable[ArtistScore]) -> bool:
    """True when no eligible video had a valid recent pair this run."""
    return not any(c.has_pair for s in scores for c in s.calcs)
