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
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger("seoulpulse.http")

MAX_BYTES = 5 * 1024 * 1024
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF = (1.0, 4.0)          # sleeps before retry 1 and retry 2
MAX_RETRY_AFTER = 30.0
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Per-attempt wall-clock budget for receiving a whole body. httpx's read timeout is per read, so a server
# that trickles one byte every few seconds would otherwise never time out.
TOTAL_TIMEOUT = 20.0
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
    """Transport failure after retries (timeouts, connection errors, oversize bodies, redirect loops)."""


class _TotalTimeout(httpx.ReadTimeout):
    """The per-attempt total budget ran out while a body was still trickling in (retryable like a read timeout)."""


class _DeadlineExceeded(Exception):
    """The run-wide hard deadline passed; never retried."""


def _same_site(a: str, b: str) -> bool:
    """Coarse registrable-domain check used to decide whether cookies may follow a redirect."""
    return a.split(".")[-2:] == b.split(".")[-2:]


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
    """Sequential-per-host GETs with pacing, bounded bodies, retries and manually followed redirects.

    Every redirect hop is treated like a fresh request to its own host: it takes that host's lock, waits
    for that host's pacing interval, and its body is read under the same byte cap and time budget.
    """

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
        total_timeout: float = TOTAL_TIMEOUT,
        deadline: float | None = None,
    ):
        self.user_agent = user_agent
        self._sleep = sleep
        self._clock = clock
        self._rand = rand
        self._max_bytes = max_bytes
        self._pacing = pacing
        self._total_timeout = total_timeout
        self.deadline = deadline            # absolute value on `clock`; no request work happens after it
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,         # redirects are followed by hand, see get()
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

    def _time_left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - self._clock()

    def _check_deadline(self) -> None:
        if self._time_left() <= 0:
            raise _DeadlineExceeded()

    def _sleep_bounded(self, seconds: float) -> None:
        if seconds <= 0:
            return
        left = self._time_left()
        if seconds >= left:
            self._sleep(max(0.0, left))
            raise _DeadlineExceeded()
        self._sleep(seconds)

    def _pace(self, host: str) -> None:
        interval = self._pacing(host)
        last = self._last.get(host)
        if last is not None:
            wait = last + interval - self._clock()
            if wait > 0:
                self._sleep_bounded(wait)
        self._last[host] = self._clock()

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        validators: tuple[str | None, str | None] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> Response:
        """GET with retries and redirects. Returns any final HTTP status; raises HttpError on transport failure."""
        req_headers = dict(headers or {})
        if validators:
            etag, last_modified = validators
            if etag:
                req_headers["If-None-Match"] = etag
            if last_modified:
                req_headers["If-Modified-Since"] = last_modified
        origin = urlsplit(url).hostname or ""
        try:
            current = url
            for _hop in range(MAX_REDIRECTS + 1):
                host = urlsplit(current).hostname or ""
                hop_headers = dict(req_headers)
                if cookies and _same_site(origin, host):
                    hop_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
                resp = self._get_one(current, host, hop_headers)
                location = resp.headers.get("location")
                if resp.status not in REDIRECT_STATUSES or not location:
                    return resp
                nxt = urljoin(current, location.strip())
                if urlsplit(nxt).scheme not in ("http", "https") or not urlsplit(nxt).hostname:
                    raise HttpError(f"refusing redirect to {redact(nxt)[:120]}")
                current = nxt
            raise HttpError(f"too many redirects (>{MAX_REDIRECTS})")
        except _DeadlineExceeded:
            raise HttpError("run deadline exceeded") from None

    def _get_one(self, url: str, host: str, headers: Mapping[str, str]) -> Response:
        """One hop: host lock + pacing + retries + bounded body."""
        attempts = len(BACKOFF) + 1
        with self._host_lock(host):
            for attempt in range(attempts):
                self._check_deadline()
                self._pace(host)
                try:
                    resp = self._fetch(url, headers)
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
        self._sleep_bounded(delay)

    def _fetch(self, url: str, headers: Mapping[str, str]) -> Response:
        started = self._clock()
        with self._client.stream("GET", url, headers=headers) as r:
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                raise httpx.DecodingError(f"response too large ({declared} bytes)")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > self._max_bytes:
                    raise httpx.DecodingError(f"response exceeds {self._max_bytes} bytes")
                if self._clock() - started > self._total_timeout:
                    raise _TotalTimeout(f"body not complete after {self._total_timeout:.0f}s")
                self._check_deadline()
            return Response(url=str(r.url), status=r.status_code, content=bytes(buf),
                            headers={k.lower(): v for k, v in r.headers.items()})
