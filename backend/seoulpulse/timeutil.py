"""UTC helpers. Everything internal and in JSON is UTC, `YYYY-MM-DDTHH:MM:SSZ` (spec §0.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """Format an aware (or naive-UTC) datetime as `YYYY-MM-DDTHH:MM:SSZ`."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime(ISO_FMT)


def parse_iso(s: str) -> datetime:
    """Parse ISO-8601 (with `Z` or an offset, optional fraction) into an aware UTC datetime."""
    s = s.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def hours(td: timedelta) -> float:
    return td.total_seconds() / 3600.0


def day_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def parse_day(s: str) -> date:
    return date.fromisoformat(s)
