"""Social post drafts for manual posting (spec §9). We never post anything."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from .timeutil import parse_iso

try:
    from zoneinfo import ZoneInfo

    KST: timezone | ZoneInfo = ZoneInfo("Asia/Seoul")
except Exception:  # noqa: BLE001 - no tz database: Korea has had no DST since 1988
    KST = timezone(timedelta(hours=9), "KST")

FLAG = "\U0001f1f0\U0001f1f7"  # 🇰🇷
X_LIMIT = 280
THREADS_LIMIT = 500
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
THREADS_LONG_FOOTER = (
    f"{FLAG} = Korean sources moved before English-language coverage.\n"
    "Signals: YouTube view velocity, Wikipedia pageviews, KR/EN headline counts. Fan-made, not affiliated."
)
THREADS_SHORT_FOOTER = f"{FLAG} = Korea moved first · fan-made, not affiliated"


class NothingToPost(Exception):
    pass


def x_weight(text: str) -> int:
    """Conservative X weighting: 1 for U+0000–10FF, 2000–200D, 2010–201F, 2032–2037; 2 otherwise."""
    total = 0
    for ch in text:
        cp = ord(ch)
        if cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F or 0x2032 <= cp <= 0x2037:
            total += 1
        else:
            total += 2
    return total


def delta_token(item: Mapping[str, Any]) -> str:
    change = item.get("change")
    amount = int(item.get("change_amount") or 0)
    if change == "new":
        return "NEW"
    if change == "up":
        return f"▲{amount}"
    if change == "down":
        return f"▼{amount}"
    return ""


def _kst(snapshot: Mapping[str, Any]) -> datetime:
    return parse_iso(str(snapshot["generated_at"])).astimezone(KST)


def date_label(dt: datetime) -> str:
    return f"{MONTHS[dt.month - 1]} {dt.day}"


def _item_head(item: Mapping[str, Any], with_ko: bool = False) -> str:
    parts = [f"{item['rank']}. {item['name']}"]
    if with_ko and item.get("name_ko"):
        parts.append(f"({item['name_ko']})")
    d = delta_token(item)
    if d:
        parts.append(d)
    if item.get("korea_first"):
        parts.append(FLAG)
    return " ".join(parts)


def build_x(snapshot: Mapping[str, Any], top: int = 5) -> str:
    items = _top_items(snapshot, top)
    dt = _kst(snapshot)
    heads = [_item_head(it) for it in items]

    def render(lines: Sequence[str]) -> str:
        return "\n".join([f"Korea-first K-pop TOP {len(items)} · {date_label(dt)}", "", *lines, "",
                          f"{FLAG} = Korea moved first", "#kpop #SeoulRising"])

    lines = list(heads)
    for i, it in enumerate(items):
        trial = list(lines)
        trial[i] = f"{heads[i]} — {it.get('why', '')}"
        if x_weight(render(trial)) > X_LIMIT:
            break
        lines = trial
    return render(lines)


def build_threads(snapshot: Mapping[str, Any], top: int = 5) -> str:
    items = _top_items(snapshot, top)
    dt = _kst(snapshot)
    header = f"Korea-first K-pop Trends — TOP {len(items)} ({date_label(dt)}, {dt:%H:%M} KST)"

    def render(with_ko: bool, footer: str, why_max: int | None) -> str:
        out = [header, ""]
        for it in items:
            why = str(it.get("why", ""))
            if why_max is not None and len(why) > why_max:
                why = why[:why_max] + "…"
            out.append(_item_head(it, with_ko))
            out.append(f"   {why}")
        out += ["", footer]
        return "\n".join(out)

    variants = (
        render(True, THREADS_LONG_FOOTER, None),
        render(False, THREADS_LONG_FOOTER, None),
        render(False, THREADS_SHORT_FOOTER, None),
        render(False, THREADS_SHORT_FOOTER, 40),
    )
    for v in variants:
        if len(v) <= THREADS_LIMIT:
            return v
    return variants[-1]


def _top_items(snapshot: Mapping[str, Any], top: int) -> list[Mapping[str, Any]]:
    items = sorted(snapshot.get("items") or [], key=lambda it: it.get("rank", 0))
    if not items:
        raise NothingToPost("nothing to post")
    return items[: max(1, top)]


def render_drafts(snapshot: Mapping[str, Any], top: int = 5, fmt: str = "both") -> str:
    blocks = []
    if fmt in ("x", "both"):
        x = build_x(snapshot, top)
        blocks.append(f"=== X ({x_weight(x)}/{X_LIMIT}) ===\n{x}")
    if fmt in ("threads", "both"):
        t = build_threads(snapshot, top)
        blocks.append(f"=== Threads ({len(t)}/{THREADS_LIMIT}) ===\n{t}")
    return "\n\n".join(blocks) + "\n"
