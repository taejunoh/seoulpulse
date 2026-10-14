"""Shared HTTP client: UA, timeouts, retries, per-host pacing, conditional GET (spec §3.1)."""

from __future__ import annotations

import email.utils
import logging
import random
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("seoulpulse.http")

MAX_BYTES = 5 * 1024 * 1024
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF = (1.0, 4.0)          # sleeps before retry 1 and retry 2
MAX_RETRY_AFTER = 30.0
NEWS_HOSTS = ("soompi.com", "koreaboo.com", "yna.co.kr")
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
_KEY_RE = re.compile(r"([?&]key=)[^&\s]+")


def redact(text: str) -> str:
    """Strip API keys from URLs before they reach logs or source_runs.error."""
    return _KEY_RE.sub(r"\1REDACTED", text)


class HttpError(Exception):
    """Transport failure after retries (timeouts, connection errors, oversize bodies)."""


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    content: bytes
    headers: Mapping[str, str]

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def pace_interval(host: str) -> float:
    host = host.lower()
    if host in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        return 0.5
    if host == "wikimedia.org" or host.endswith(".wikimedia.org"):
        return 0.4
    if host.endswith(".wikipedia.org"):
        return 1.0
    if host.endswith("googleapis.com"):
        return 0.2
    if any(host == h or host.endswith("." + h) for h in NEWS_HOSTS):
        return 1.0
    return 1.0


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max(0.0, (dt - now).total_seconds())


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
        max_bytes: int = MAX_BYTES,
        pacing: Callable[[str], float] = pace_interval,
    ):
        self.user_agent = user_agent
        self._sleep = sleep
        self._clock = clock
        self._rand = rand
        self._max_bytes = max_bytes
        self._pacing = pacing
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            headers={"User-Agent": user_agent},
        )
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._meta_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _host_lock(self, host: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._locks.get(host)
            if lock is None:
                lock = self._locks[host] = threading.Lock()
            return lock

    def _pace(self, host: str) -> None:
        interval = self._pacing(host)
        last = self._last.get(host)
        if last is not None:
            wait = last + interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        validators: tuple[str | None, str | None] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> Response:
        """GET with retries. Returns any HTTP status; raises HttpError on transport failure."""
        host = urlsplit(url).hostname or ""
        req_headers = dict(headers or {})
        if validators:
            etag, last_modified = validators
            if etag:
                req_headers["If-None-Match"] = etag
            if last_modified:
                req_headers["If-Modified-Since"] = last_modified
        if cookies:
            req_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

        attempts = len(BACKOFF) + 1
        with self._host_lock(host):
            for attempt in range(attempts):
                self._pace(host)
                try:
                    resp = self._fetch(url, req_headers)
                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout, httpx.WriteTimeout) as exc:
                    if attempt + 1 < attempts:
                        self._backoff(attempt, None)
                        continue
                    raise HttpError(f"timeout after {attempts - 1} retries") from exc
                except httpx.HTTPError as exc:
                    raise HttpError(redact(f"{type(exc).__name__}: {exc}")[:200]) from exc
                if resp.status in RETRY_STATUSES and attempt + 1 < attempts:
                    self._backoff(attempt, parse_retry_after(resp.headers.get("retry-after")))
                    continue
                return resp
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            delay = min(retry_after, MAX_RETRY_AFTER)
        else:
            delay = BACKOFF[attempt] + self._rand() * 0.5
        log.debug("retrying in %.2fs", delay)
        self._sleep(delay)

    def _fetch(self, url: str, headers: Mapping[str, str]) -> Response:
        with self._client.stream("GET", url, headers=headers) as r:
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                raise httpx.DecodingError(f"response too large ({declared} bytes)")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > self._max_bytes:
                    raise httpx.DecodingError(f"response exceeds {self._max_bytes} bytes")
            return Response(url=str(r.url), status=r.status_code, content=bytes(buf),
                            headers={k.lower(): v for k, v in r.headers.items()})
