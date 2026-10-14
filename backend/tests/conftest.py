"""Shared fixtures. Tests never touch the live network: an autouse fixture fails any real socket."""

from __future__ import annotations

import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seoulpulse.config import BACKEND_DIR, REPO_DIR, load_config

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = REPO_DIR / "docs"
SCHEMA_PATH = DOCS / "trends.schema.json"
EXAMPLE_PATH = DOCS / "trends.example.json"
DATA_DIR = BACKEND_DIR / "data"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


class _BlockedSocket(socket.socket):
    def connect(self, address):  # type: ignore[override]
        host = address[0] if isinstance(address, tuple) else address
        if getattr(_BlockedSocket, "allow_loopback", False) and host in ("127.0.0.1", "::1", "localhost"):
            return super().connect(address)
        raise RuntimeError(f"tests must not open real sockets (tried {address!r})")

    def connect_ex(self, address):  # type: ignore[override]
        self.connect(address)
        return 0


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    allow = request.node.get_closest_marker("allow_loopback") is not None
    _BlockedSocket.allow_loopback = allow
    monkeypatch.setattr(socket, "socket", _BlockedSocket)

    real_create = socket.create_connection

    def guarded_create(address, *a, **kw):
        if allow and address[0] in ("127.0.0.1", "::1", "localhost"):
            return real_create(address, *a, **kw)
        raise RuntimeError(f"tests must not open real sockets (tried {address!r})")

    monkeypatch.setattr(socket, "create_connection", guarded_create)

    def blocked_getaddrinfo(host, *a, **kw):
        if allow and host in ("127.0.0.1", "::1", "localhost"):
            return real_getaddrinfo(host, *a, **kw)
        raise RuntimeError(f"tests must not resolve hosts (tried {host!r})")

    real_getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)
    yield


class FakeClock:
    """Monotonic clock + sleep that only advance when sleep() is called."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += max(0.0, s)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def config(tmp_path):
    """A Config whose DB and public dir live in tmp_path; seed data is the committed roster."""
    cfg = load_config({"SP_CONTACT": "https://example.invalid/seoulpulse-tests"}, warn=False)
    return replace(cfg, db_path=tmp_path / "var" / "seoulpulse.db", public_dir=tmp_path / "public")


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
