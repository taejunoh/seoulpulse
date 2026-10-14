"""UTC helpers. Everything internal and in JSON is UTC, `YYYY-MM-DDTHH:MM:SSZ` (spec §0.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _zone(name: str, fallback: tzinfo) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database available
        return fallback


# Korea has had no DST since 1988. Pacific time is only used to find the YouTube quota day; the fixed
# fallback (PST) can at worst start that day one hour early, which only makes the budget stricter.
KST = _zone("Asia/Seoul", timezone(timedelta(hours=9), "KST"))
PACIFIC = _zone("America/Los_Angeles", timezone(timedelta(hours=-8), "PST"))


def kst_date(dt: datetime) -> date:
    """Calendar day in Korea (Naver DataLab days and its quota day are KST)."""
    return dt.astimezone(KST).date()


def pacific_date(dt: datetime) -> date:
    """YouTube Data API quota day: quotas reset at midnight Pacific time."""
    return dt.astimezone(PACIFIC).date()


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
