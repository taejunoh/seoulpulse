"""Tiny static server for public/ (127.0.0.1 by default) with `Cache-Control: no-cache` (spec §1, §11)."""

from __future__ import annotations

import functools
import logging
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("seoulpulse.serve")


class NoCacheHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".json": "application/json; charset=utf-8"}

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        log.info("%s %s", self.address_string(), format % args)


def seed_example(public_dir: Path, example: Path) -> bool:
    """Copy docs/trends.example.json to public/trends.json if it is missing. Returns True if copied."""
    target = public_dir / "trends.json"
    if target.exists():
        return False
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, target)
    return True


def make_server(public_dir: Path, port: int = 8787, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    public_dir.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(NoCacheHandler, directory=str(public_dir))
    return ThreadingHTTPServer((host, port), handler)


def serve_forever(public_dir: Path, port: int = 8787, host: str = "127.0.0.1") -> None:
    server = make_server(public_dir, port, host)
    log.info("serving %s at http://%s:%d/trends.json", public_dir, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def serve_in_thread(
    public_dir: Path, port: int = 8787, host: str = "127.0.0.1"
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = make_server(public_dir, port, host)
    t = threading.Thread(target=server.serve_forever, name="serve", daemon=True)
    t.start()
    log.info("serving %s at http://%s:%d/trends.json", public_dir, host, port)
    return server, t
