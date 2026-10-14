"""Load and validate artists.yaml, channels.yaml and channels.lock.json (spec §2)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from .matching import auto_case_sensitive, hangul_count, normalize

DENY_LIST = frozenset("있지 아이들 정국 지수 빌리 비비 아홉 청하 방탄 진 뷔".split())
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
UC_RE = re.compile(r"^UC[\w-]{22}$")
HANDLE_RE = re.compile(r"^@[\w.\-]{2,}$")
CHANNEL_KINDS = ("label", "broadcaster")


class SeedError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:5]) + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""))
        self.errors = errors


@dataclass(frozen=True)
class Alias:
    term: str
    case_sensitive: bool  # effective (explicit flag or the automatic short-uppercase rule)


@dataclass(frozen=True)
class Artist:
    id: str
    name: str
    name_ko: str | None
    aliases: tuple[Alias, ...]
    youtube: tuple[str, ...]
    wiki_en: str | None
    wiki_ko: str | None

    def wiki_title(self, lang: str) -> str | None:
        return self.wiki_en if lang == "en" else self.wiki_ko if lang == "ko" else None


@dataclass(frozen=True)
class Channel:
    handle: str            # "@handle" or a literal "UC…" id
    kind: str              # artist | label | broadcaster
    name: str              # yaml name (label/broadcaster) or the owner's display name (artist)
    owner: str | None      # artist id for kind=artist
    channel_id: str | None
    title: str | None      # resolved channel title from the lock file

    @property
    def display_name(self) -> str:
        return self.title or self.name


@dataclass(frozen=True)
class Seed:
    artists: tuple[Artist, ...]
    channels: tuple[Channel, ...]
    lock: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: tuple[str, ...] = ()        # hard validation errors
    warnings: tuple[str, ...] = ()      # e.g. unresolved handles

    @cached_property
    def by_id(self) -> dict[str, Artist]:
        return {a.id: a for a in self.artists}

    @cached_property
    def channel_by_id(self) -> dict[str, Channel]:
        out: dict[str, Channel] = {}
        # Artist channels first so they win if a label channel shares an id.
        for ch in sorted(self.channels, key=lambda c: 0 if c.kind == "artist" else 1):
            if ch.channel_id and ch.channel_id not in out:
                out[ch.channel_id] = ch
        return out

    def resolved_channels(self) -> list[Channel]:
        return list(self.channel_by_id.values())

    def artist_channel_url(self, artist_id: str) -> str | None:
        artist = self.by_id.get(artist_id)
        if artist is None:
            return None
        for handle in artist.youtube:
            cid = self.lookup_channel_id(handle)
            if cid:
                return f"https://www.youtube.com/channel/{cid}"
        return None

    def lookup_channel_id(self, handle: str) -> str | None:
        return _lookup(handle, self.lock)[0]


def _lookup(handle: str, lock: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    if UC_RE.match(handle):
        return handle, None
    entry = lock.get(handle)
    if entry is None:
        low = handle.casefold()
        entry = next((v for k, v in lock.items() if k.casefold() == low), None)
    if entry and UC_RE.match(str(entry.get("channel_id", ""))):
        return entry["channel_id"], entry.get("title")
    return None, None


def _parse_alias(raw: Any, where: str, errors: list[str]) -> Alias | None:
    if isinstance(raw, str):
        term, explicit = raw, False
    elif isinstance(raw, dict) and isinstance(raw.get("term"), str):
        term, explicit = raw["term"], bool(raw.get("case_sensitive", False))
    else:
        errors.append(f"{where}: alias must be a string or {{term, case_sensitive}}: {raw!r}")
        return None
    term = normalize(term)
    if not term:
        errors.append(f"{where}: empty alias")
        return None
    return Alias(term=term, case_sensitive=explicit or auto_case_sensitive(term))


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def load_lock(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    chans = data.get("channels", {}) if isinstance(data, dict) else {}
    return chans if isinstance(chans, dict) else {}


def load_seed(data_dir: Path, *, strict: bool = True) -> Seed:
    """Load the roster. With strict=True, hard errors raise SeedError."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        artists_raw = yaml.safe_load((data_dir / "artists.yaml").read_text(encoding="utf-8")) or {}
        channels_raw = yaml.safe_load((data_dir / "channels.yaml").read_text(encoding="utf-8")) or {}
        lock = load_lock(data_dir / "channels.lock.json")
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise SeedError([f"cannot read seed files: {exc}"]) from exc

    if artists_raw.get("version") != 1:
        errors.append("artists.yaml: version must be 1")
    if channels_raw.get("version") != 1:
        errors.append("channels.yaml: version must be 1")

    artists: list[Artist] = []
    seen_ids: set[str] = set()
    for i, a in enumerate(artists_raw.get("artists") or []):
        where = f"artists[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{where}: not a mapping")
            continue
        aid = str(a.get("id", ""))
        where = f"artist {aid or i}"
        if not SLUG_RE.match(aid) or len(aid) > 40:
            errors.append(f"{where}: id must match ^[a-z0-9]+(-[a-z0-9]+)*$ (≤40 chars)")
        if aid in seen_ids:
            errors.append(f"{where}: duplicate id")
        seen_ids.add(aid)
        name = _opt_str(a.get("name"))
        if not name:
            errors.append(f"{where}: name is required")
            name = aid
        aliases = tuple(x for x in (_parse_alias(r, where, errors) for r in (a.get("aliases") or [])) if x)
        if not aliases:
            errors.append(f"{where}: at least one alias is required")
        yt = a.get("youtube") or []
        if not isinstance(yt, list):
            errors.append(f"{where}: youtube must be a list")
            yt = []
        for h in yt:
            if not (isinstance(h, str) and (HANDLE_RE.match(h) or UC_RE.match(h))):
                errors.append(f"{where}: bad youtube handle {h!r}")
        wiki = a.get("wiki") or {}
        if not isinstance(wiki, dict):
            errors.append(f"{where}: wiki must be a mapping")
            wiki = {}
        artists.append(Artist(
            id=aid, name=name, name_ko=_opt_str(a.get("name_ko")), aliases=aliases,
            youtube=tuple(str(h) for h in yt), wiki_en=_opt_str(wiki.get("en")), wiki_ko=_opt_str(wiki.get("ko")),
        ))

    errors.extend(check_alias_rules(artists))

    channels: list[Channel] = []
    for a in artists:
        for h in a.youtube:
            cid, title = _lookup(h, lock)
            if cid is None:
                warnings.append(f"artist {a.id}: channel {h} has no id in channels.lock.json")
            channels.append(Channel(handle=h, kind="artist", name=a.name, owner=a.id, channel_id=cid, title=title))
    for i, c in enumerate(channels_raw.get("channels") or []):
        if not isinstance(c, dict):
            errors.append(f"channels[{i}]: not a mapping")
            continue
        h, kind, name = str(c.get("handle", "")), str(c.get("kind", "")), _opt_str(c.get("name"))
        where = f"channel {h or i}"
        if not (HANDLE_RE.match(h) or UC_RE.match(h)):
            errors.append(f"{where}: bad handle")
        if kind not in CHANNEL_KINDS:
            errors.append(f"{where}: kind must be label|broadcaster")
        if not name:
            errors.append(f"{where}: name is required")
        cid, title = _lookup(h, lock)
        if cid is None:
            warnings.append(f"{where}: no id in channels.lock.json")
        channels.append(Channel(handle=h, kind=kind, name=name or h, owner=None, channel_id=cid, title=title))

    seed = Seed(artists=tuple(artists), channels=tuple(channels), lock=lock,
                issues=tuple(errors), warnings=tuple(warnings))
    if strict and errors:
        raise SeedError(errors)
    return seed


def check_alias_rules(artists: list[Artist]) -> list[str]:
    errors: list[str] = []
    owners: dict[str, str] = {}
    for a in artists:
        for al in a.aliases:
            term = al.term
            if hangul_count(term) > 0:
                if hangul_count(term) < 2:
                    errors.append(f"artist {a.id}: Hangul alias {term!r} needs ≥2 syllables")
                if term in DENY_LIST:
                    errors.append(f"artist {a.id}: alias {term!r} is on the ambiguous-word deny-list")
            elif len(term) < 2:
                errors.append(f"artist {a.id}: alias {term!r} needs ≥2 characters")
            key = term.casefold()
            prev = owners.get(key)
            if prev is not None and prev != a.id:
                errors.append(f"alias {term!r} is shared by {prev} and {a.id}")
            owners.setdefault(key, a.id)
    return errors
