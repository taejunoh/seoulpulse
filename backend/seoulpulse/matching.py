"""Alias matching, video classification and song extraction (spec §2.3)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

_WS = re.compile(r"\s+")

# Latin/other boundary class `L` and Hangul syllables.
L_CLASS = "A-Za-z0-9À-ɏ"
HANGUL_CLASS = "가-힣"  # 가-힣
PARTICLES = (
    "으로", "에게", "에서", "이랑", "까지", "부터", "처럼", "보다",
    "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "로", "에", "랑", "만", "측",
)


def normalize(s: str) -> str:
    """NFKC, collapse whitespace, strip."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", s)).strip()


def is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def hangul_count(s: str) -> int:
    return sum(1 for ch in s if is_hangul(ch))


def auto_case_sensitive(term: str) -> bool:
    """ASCII aliases that are all uppercase and <=4 non-space characters match case-sensitively."""
    compact = term.replace(" ", "")
    return term.isascii() and term.isupper() and len(compact) <= 4


def alias_regex(term: str, case_sensitive: bool) -> re.Pattern[str]:
    term = normalize(term)
    if not term:
        raise ValueError("empty alias")
    body = r"\s+".join(re.escape(part) for part in term.split(" "))
    left = f"(?<![{HANGUL_CLASS}])" if is_hangul(term[0]) else f"(?<![{L_CLASS}])"
    if is_hangul(term[-1]):
        particles = "|".join(PARTICLES)
        right = f"(?=(?:{particles})?(?![{HANGUL_CLASS}]))"
    else:
        right = f"(?![{L_CLASS}])"
    return re.compile(left + body + right, 0 if case_sensitive else re.IGNORECASE)


class _ArtistLike(Protocol):
    id: str

    @property
    def aliases(self) -> Sequence: ...


@dataclass(frozen=True)
class Match:
    artist_id: str
    start: int
    end: int
    term: str

    @property
    def length(self) -> int:
        return self.end - self.start


class AliasMatcher:
    """Matches every artist's aliases with overlap resolution (longest first, then earliest)."""

    def __init__(self, artists: Iterable[_ArtistLike]):
        self._patterns: list[tuple[str, str, re.Pattern[str]]] = []
        for artist in artists:
            for alias in artist.aliases:
                self._patterns.append((artist.id, alias.term, alias_regex(alias.term, alias.case_sensitive)))

    def matches(self, text: str) -> list[Match]:
        t = normalize(text)
        if not t:
            return []
        cands: list[Match] = []
        for artist_id, term, pat in self._patterns:
            for m in pat.finditer(t):
                cands.append(Match(artist_id, m.start(), m.end(), term))
        cands.sort(key=lambda m: (-m.length, m.start, m.artist_id))
        accepted: list[Match] = []
        for m in cands:
            if all(m.end <= a.start or m.start >= a.end for a in accepted):
                accepted.append(m)
        accepted.sort(key=lambda m: (m.start, m.artist_id))
        return accepted

    def mentions(self, text: str) -> set[str]:
        """Set of artist ids mentioned in a headline (each counts at most once)."""
        return {m.artist_id for m in self.matches(text)}

    def primary(self, text: str) -> str | None:
        """Artist of the accepted match with the smallest start offset."""
        found = self.matches(text)
        return found[0].artist_id if found else None


# ---------------------------------------------------------------------------- classify_video

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Addition to spec §2.3 (rule 1b): the acts' own variety series and concert live-viewing promos are `other`
    # even when announced with a "Teaser"/"Trailer" ("[TIME TO TWICE] 트둥의 숲 TDOONG's Crossing Teaser",
    # "BTS WORLD TOUR … LIVE VIEWING - Special Trailer" both read as "New teaser: comeback incoming").
    ("other", re.compile(
        r"time to twice|going seventeen|to do x txt|skz code|run bts|bangtan bomb|dada-itzy|live viewing",
        re.IGNORECASE)),
    ("teaser", re.compile(
        r"teaser|티저|trailer|트레일러|spoiler|스포일러|highlight medley|하이라이트 메들리|concept film"
        r"|preview|sneak peek|coming soon|teasing (?:video|film|clip)", re.IGNORECASE)),
    ("other", re.compile(
        r"behind|비하인드|making|메이킹|reaction|리액션|vlog|브이로그|interview|인터뷰|unboxing", re.IGNORECASE)),
    ("mv", re.compile(r"\bm/?v\b|music video|뮤직비디오|official video", re.IGNORECASE)),
    ("perf", re.compile(
        r"performance|퍼포먼스|dance practice|안무|choreography|special video|visualizer|lyric video"
        r"|official audio|live clip|relay dance|릴레이 댄스|fancam|직캠", re.IGNORECASE)),
    # Deviation from spec §2.3: the spec list also had `ep\.\s?\d+`. On artist/label channels that matches
    # self-produced variety series ("[EP.08] 아우디즈의 찾아서 투어", "[GOING SEVENTEEN] EP.163", "TO DO X TXT
    # EP.175"), which are not music-show stages. Real music-show uploads always carry the show's name, so
    # episodic titles without one now fall through to `other`. Show names also tolerate a missing space
    # ("Show! MusicCore").
    ("show", re.compile(
        r"엠카운트다운|m\s?countdown|인기가요|inkigayo|music\s?bank|뮤직뱅크|음악중심|music\s?core|show!?\s?champion"
        r"|쇼챔피언|\bthe show\b|더쇼|studio choom|it['’]s live|방송", re.IGNORECASE)),
    # Addition to spec §2.3 (rule 6b): a stage that is not a Korean music-show upload (concert "Stage Video",
    # overseas TV "stage @ CDTV") is a performance, not "other". It runs after `show`, so "Comeback Stage |
    # M COUNTDOWN" stays a music-show stage; "backstage" has no word boundary and never matches.
    ("perf", re.compile(r"\bstage\b|스테이지", re.IGNORECASE)),
)

VIDEO_KINDS = ("short", "mv", "perf", "teaser", "show", "other")


def classify_video(title: str, url: str, channel_kind: str | None = None) -> str:
    if "/shorts/" in (url or ""):
        return "short"
    t = normalize(title or "")
    for kind, pat in _RULES:
        if pat.search(t):
            # Addition to spec §2.3 (rule 2): on a broadcaster channel a teaser/trailer/preview promotes the
            # broadcaster's own programme (KBS Kpop "[Teaser] … MEOVV Mood Sampler", a festival line-up), never
            # an act's comeback, so it must not read as "New teaser: comeback incoming".
            if kind == "teaser" and channel_kind == "broadcaster":
                return "other"
            return kind
    return "other"


# ---------------------------------------------------------------------------- extract_song

_QUOTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile("‘([^’']+?)[’']"),   # ‘…’ or ‘…'
    re.compile("“([^”\"]+?)[”\"]"),  # “…” or “…"
    re.compile("「([^」]+?)」"),          # 「…」
    re.compile("『([^』]+?)』"),          # 『…』
    re.compile("《([^》]+?)》"),          # 《…》
    re.compile("<([^>]+?)>"),
    re.compile('"([^"]+?)"'),
    re.compile(r"'([^']+?)'(?![A-Za-z])"),
)
_TRAILING_BRACKET = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])\s*$")
SONG_MAX = 24


def truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def extract_song(title: str) -> str | None:
    t = normalize(title or "")
    best: re.Match[str] | None = None
    for pat in _QUOTE_PATTERNS:
        m = pat.search(t)
        if m and m.group(1).strip() and (best is None or m.start() < best.start()):
            best = m
    song: str | None = None
    if best is not None:
        song = best.group(1).strip()
    else:
        rest = None
        if " _ " in t:
            rest = t.split(" _ ", 1)[1]
        elif " - " in t:
            rest = t.split(" - ", 1)[1]
        if rest is not None:
            prev = None
            while prev != rest:
                prev = rest
                rest = _TRAILING_BRACKET.sub("", rest)
            song = rest.strip() or None
    if not song:
        return None
    return truncate(song, SONG_MAX)
