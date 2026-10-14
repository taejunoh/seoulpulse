"""Headline RSS feeds: Yonhap (ko), Soompi and Koreaboo (en) (spec §3.5).

Legal rule (spec §0.1): only title, link, guid and pubDate are read. `description` and
`content:encoded` are never read into any object.
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urljoin, urlsplit

from defusedxml import ElementTree as ET

from ..http import HttpError
from ..timeutil import parse_iso
from .base import CacheEntry, FetchOutput, Headline, RunContext, make_result

FEEDS = {
    "soompi": ("https://www.soompi.com/feed", "en"),
    "koreaboo": ("https://www.koreaboo.com/feed/", "en"),
    "yonhap": ("https://www.yna.co.kr/rss/entertainment.xml", "ko"),
}
TITLE_MAX = 300
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RSS1_NS = "http://purl.org/rss/1.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"


class FeedFormatError(ValueError):
    """The body parsed as XML but is not an RSS document (e.g. an XHTML consent or block page)."""
FUTURE_SLACK = timedelta(minutes=10)
_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]{1,200}>")


def headline_id(source_id: str, guid: str) -> str:
    return hashlib.sha1(f"{source_id}|{guid}".encode()).hexdigest()[:16]


def ascii_url(url: str) -> str:
    """Percent-encode non-ASCII characters and spaces so URLs match the JSON contract."""
    return quote(url.strip(), safe=":/?#[]@!$&'()*+,;=%~-._")


def clean_title(raw: str) -> str:
    t = html.unescape(raw or "")
    t = _TAG.sub("", t) if "<" in t else t
    t = _WS.sub(" ", t).strip()
    return t[:TITLE_MAX]


def parse_pubdate(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            return parse_iso(raw)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0)


def _child_text(item, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "") if el is not None else ""


def resolve_link(raw: str, base_url: str | None) -> str | None:
    """Absolute http(s) URL for an item link, or None for unsupported/unusable links."""
    raw = (raw or "").strip()
    if not raw:
        return None
    url = urljoin(base_url, raw) if base_url else raw
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return ascii_url(url)


def _rss_items(root) -> tuple[list, dict[str, str]]:
    """Return (items, tag names) for RSS 2.0 or RSS 1.0 (RDF); raise FeedFormatError for anything else."""
    if root.tag == "rss":
        channel = root.find("channel")
        if channel is None:
            raise FeedFormatError("rss document without <channel>")
        return channel.findall("item"), {"title": "title", "link": "link", "guid": "guid", "date": "pubDate"}
    if root.tag == f"{{{RDF_NS}}}RDF":
        if root.find(f"{{{RSS1_NS}}}channel") is None:
            raise FeedFormatError("RDF document without an RSS 1.0 channel")
        return root.findall(f"{{{RSS1_NS}}}item"), {
            "title": f"{{{RSS1_NS}}}title", "link": f"{{{RSS1_NS}}}link", "guid": f"{{{RSS1_NS}}}link",
            "date": f"{{{DC_NS}}}date",
        }
    raise FeedFormatError(f"not an RSS feed (root element <{root.tag}>)")


def parse_rss(xml: bytes, source_id: str, lang: str, fetched_at: datetime, *,
              base_url: str | None = None) -> list[Headline]:
    """Parse an RSS 2.0/1.0 feed. Item links are resolved against `base_url` (default: the source's feed URL);
    items whose link is not absolute http(s) after resolution are dropped."""
    if base_url is None and source_id in FEEDS:
        base_url = FEEDS[source_id][0]
    root = ET.fromstring(xml.lstrip(b"\xef\xbb\xbf \t\r\n"))
    items, tags = _rss_items(root)
    out: list[Headline] = []
    seen: set[str] = set()
    for item in items:
        # Only title, link, guid and the date are ever touched; description/content:encoded never are.
        title = clean_title(_child_text(item, tags["title"]))
        link = resolve_link(_child_text(item, tags["link"]), base_url)
        if not title or link is None:
            continue
        guid = _child_text(item, tags["guid"]).strip() or link
        hid = headline_id(source_id, guid)
        if hid in seen:
            continue
        seen.add(hid)
        pub = parse_pubdate(_child_text(item, tags["date"]))
        if pub is not None and pub > fetched_at + FUTURE_SLACK:
            pub = None
        out.append(Headline(headline_id=hid, source_id=source_id, lang=lang, title=title,
                            url=link, guid=guid, pub_date=pub, fetched_at=fetched_at))
    return out


class NewsFetcher:
    def __init__(self, source_id: str, url: str | None = None, lang: str | None = None):
        default_url, default_lang = FEEDS[source_id]
        self.source_id = source_id
        self.url = url or default_url
        self.lang = lang or default_lang

    def fetch(self, ctx: RunContext) -> FetchOutput:
        started = ctx.observed()
        try:
            resp = ctx.http.get(self.url, validators=ctx.store.http_validators(self.url))
        except HttpError as exc:
            msg = str(exc)
            return FetchOutput(result=make_result(self.source_id, "error", started, ctx.observed(), 0, 1, msg, [msg]))
        if resp.status == 304:
            return FetchOutput(result=make_result(self.source_id, "ok", started, ctx.observed(), 1, 1,
                                                  "not modified (304), 0 new", []))
        if resp.status != 200:
            msg = f"HTTP {resp.status}"
            return FetchOutput(result=make_result(self.source_id, "error", started, ctx.observed(), 0, 1, msg, [msg]))
        fetched_at = ctx.observed()
        try:
            items = parse_rss(resp.content, self.source_id, self.lang, fetched_at, base_url=resp.url or self.url)
        except Exception as exc:  # noqa: BLE001 - malformed feed
            msg = f"parse error: {type(exc).__name__}: {exc}"[:200]
            return FetchOutput(result=make_result(self.source_id, "error", started, ctx.observed(), 0, 1, msg, [msg]))
        new = sum(1 for h in items if h.headline_id not in ctx.store.headline_ids)
        cache = []
        if resp.etag or resp.last_modified:
            cache.append(CacheEntry(self.url, resp.etag, resp.last_modified, fetched_at))
        result = make_result(self.source_id, "ok", started, ctx.observed(), 1, 1,
                             f"{len(items)} items, {new} new", [])
        return FetchOutput(result=result, headlines=items, http_cache=cache)
