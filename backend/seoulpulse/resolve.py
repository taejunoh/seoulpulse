"""Channel handle resolver and online seed validation (spec §2.4)."""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote, urlencode

import httpx

from .config import Config
from .fetchers.wikipedia import pageviews_url
from .fetchers.youtube_rss import FEED_URL
from .http import BROWSER_UA, HttpClient, HttpError, pace_interval
from .matching import normalize
from .seed import UC_RE, Seed, load_lock, load_seed
from .timeutil import iso, utcnow

ID_PATTERNS = (
    re.compile(r"feeds/videos\.xml\?channel_id=(UC[\w-]{22})"),
    re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})'),
    re.compile(r'"externalId":"(UC[\w-]{22})"'),
)
OG_TITLE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"', re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    handle: str
    kind: str
    owner: str | None
    expect: tuple[str, ...]   # casefolded terms, one of which must appear in the channel title


@dataclass(frozen=True)
class Resolution:
    handle: str
    status: str            # OK | MISMATCH | NOT_FOUND
    channel_id: str | None
    title: str | None
    note: str = ""


def extract_channel(page: str) -> tuple[str | None, str | None]:
    cid = None
    for pat in ID_PATTERNS:
        m = pat.search(page)
        if m:
            cid = m.group(1)
            break
    m = OG_TITLE.search(page)
    title = html.unescape(m.group(1)).strip() if m else None
    return cid, title


def title_matches(title: str | None, expect: tuple[str, ...]) -> bool:
    if not title:
        return False
    t = normalize(title).casefold()
    return any(e and e in t for e in expect)


def targets(seed: Seed) -> list[Target]:
    out: list[Target] = []
    for a in seed.artists:
        terms = tuple(normalize(al.term).casefold() for al in a.aliases)
        out.extend(Target(h, "artist", a.id, terms) for h in a.youtube)
    for ch in seed.channels:
        if ch.kind != "artist":
            out.append(Target(ch.handle, ch.kind, None, (normalize(ch.name).casefold(),)))
    return out


def resolve_one(http: HttpClient, t: Target) -> Resolution:
    if UC_RE.match(t.handle):
        return Resolution(t.handle, "OK", t.handle, None, "literal id")
    url = f"https://www.youtube.com/{quote(t.handle, safe='@._-')}"
    try:
        resp = http.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.8"},
                        cookies={"CONSENT": "YES+1"})
    except HttpError as exc:
        return Resolution(t.handle, "NOT_FOUND", None, None, str(exc))
    if resp.status != 200:
        return Resolution(t.handle, "NOT_FOUND", None, None, f"HTTP {resp.status}")
    cid, title = extract_channel(resp.text())
    if not cid:
        return Resolution(t.handle, "NOT_FOUND", None, title, "no channel id in page")
    if not title_matches(title, t.expect):
        return Resolution(t.handle, "MISMATCH", cid, title, "title does not contain an alias/name")
    try:
        feed = http.get(FEED_URL.format(cid))
    except HttpError as exc:
        return Resolution(t.handle, "NOT_FOUND", cid, title, f"feed: {exc}")
    if feed.status != 200:
        return Resolution(t.handle, "NOT_FOUND", cid, title, f"feed HTTP {feed.status}")
    return Resolution(t.handle, "OK", cid, title)


def resolve_channels(config: Config, *, force: bool = False, transport: httpx.BaseTransport | None = None,
                     sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = print) -> int:
    seed = load_seed(config.data_dir, strict=False)
    lock_path = config.data_dir / "channels.lock.json"
    lock = load_lock(lock_path)
    todo = [t for t in targets(seed) if force or (not UC_RE.match(t.handle) and t.handle not in lock)]
    if not todo:
        out(f"all {len(targets(seed))} handles already resolved in {lock_path.name} (use --force to re-check)")
        return 0
    pacing = lambda host: max(1.0, pace_interval(host))  # noqa: E731 - resolver paces at 1 request/s
    results: list[Resolution] = []
    with HttpClient(config.user_agent, transport=transport, sleep=sleep, pacing=pacing) as http:
        for t in todo:
            results.append(resolve_one(http, t))
    now = iso(utcnow())
    for r in results:
        if r.status == "OK" and r.channel_id and not UC_RE.match(r.handle):
            lock[r.handle] = {"channel_id": r.channel_id, "title": r.title, "resolved_at": now}
    data = {"generated_at": now, "channels": dict(sorted(lock.items(), key=lambda kv: kv[0].casefold()))}
    lock_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    width = max(len(r.handle) for r in results)
    for r in results:
        out(f"{r.status:<9} {r.handle:<{width}} {r.channel_id or '-':<24} {r.title or ''} {r.note}".rstrip())
    unresolved = [r for r in results if r.status != "OK"]
    out(f"{len(results) - len(unresolved)} OK, {len(unresolved)} unresolved")
    return 3 if unresolved else 0


# ---------------------------------------------------------------------------- validate-seed


def check_wiki_title(http: HttpClient, lang: str, title: str) -> str | None:
    """Return an error string, or None if the title is a canonical, non-disambiguation article."""
    q = urlencode({"action": "query", "format": "json", "redirects": "1", "prop": "pageprops",
                   "ppprop": "disambiguation", "titles": title})
    try:
        resp = http.get(f"https://{lang}.wikipedia.org/w/api.php?{q}")
    except HttpError as exc:
        return f"{lang}:{title}: {exc}"
    if resp.status != 200:
        return f"{lang}:{title}: HTTP {resp.status}"
    data = json.loads(resp.content.decode("utf-8"))
    query = data.get("query", {})
    if query.get("redirects"):
        return f"{lang}:{title}: is a redirect to {query['redirects'][0].get('to')!r} (store the canonical title)"
    for page in (query.get("pages") or {}).values():
        if "missing" in page or "invalid" in page:
            return f"{lang}:{title}: page is missing"
        if "disambiguation" in (page.get("pageprops") or {}):
            return f"{lang}:{title}: is a disambiguation page"
    return None


def validate_seed(config: Config, *, online: bool = False, transport: httpx.BaseTransport | None = None,
                  sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = print) -> int:
    seed = load_seed(config.data_dir, strict=False)
    errors: list[str] = []

    def report(msg: str) -> None:  # stream issues as they are found (online checks are slow)
        errors.append(msg)
        out(f"ERROR {msg}")

    out(f"{len(seed.artists)} artists, {len(seed.channels)} channels "
        f"({len(seed.resolved_channels())} resolved ids)")
    if not 60 <= len(seed.artists) <= 100:
        out(f"note: roster has {len(seed.artists)} acts (target 60-100)")
    for msg in [*seed.issues, *seed.warnings]:   # unresolved channel ids are errors here
        report(msg)
    if online:
        with HttpClient(config.user_agent, transport=transport, sleep=sleep) as http:
            channels = seed.resolved_channels()
            for ch in channels:
                try:
                    r = http.get(FEED_URL.format(ch.channel_id))
                    if r.status != 200:
                        report(f"channel {ch.handle} ({ch.channel_id}): feed HTTP {r.status}")
                except HttpError as exc:
                    report(f"channel {ch.handle}: {exc}")
            out(f"checked {len(channels)} channel feeds")
            end = utcnow().date() - timedelta(days=1)
            checked = 0
            for a in seed.artists:
                for lang in ("ko", "en"):
                    title = a.wiki_title(lang)
                    if not title:
                        continue
                    checked += 1
                    err = check_wiki_title(http, lang, title)
                    if err:
                        report(f"artist {a.id}: {err}")
                        continue
                    try:
                        r = http.get(pageviews_url(lang, title, end - timedelta(days=2), end))
                        if r.status != 200:
                            report(f"artist {a.id}: {lang}:{title}: pageviews HTTP {r.status}")
                    except HttpError as exc:
                        report(f"artist {a.id}: {lang}:{title}: pageviews {exc}")
                    if checked % 25 == 0:
                        out(f"checked {checked} wiki titles")
            out(f"checked {checked} wiki titles")
    out("seed OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0
