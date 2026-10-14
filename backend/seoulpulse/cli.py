"""`seoulpulse` command-line entrypoint (spec §1)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import load_config
from .fetchers.base import OPTIONAL_SOURCE_IDS, SOURCE_IDS
from .timeutil import parse_iso

log = logging.getLogger("seoulpulse")
ALL_SOURCE_IDS = SOURCE_IDS + OPTIONAL_SOURCE_IDS


def _setup_logging(verbose: bool) -> None:
    logging.Formatter.converter = time.gmtime  # log timestamps in UTC, like everything else
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parse_sources(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    names = [s.strip() for s in raw.split(",") if s.strip()]
    bad = [s for s in names if s not in ALL_SOURCE_IDS]
    if bad:
        raise SystemExit(f"unknown source(s): {', '.join(bad)} (valid: {', '.join(ALL_SOURCE_IDS)})")
    return names


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run_loop, run_once

    config = load_config()
    sources = _parse_sources(args.sources)
    if sources and "naver" in sources and not config.naver_enabled:
        raise SystemExit("source naver needs NAVER_CLIENT_ID and NAVER_CLIENT_SECRET")
    if args.loop:
        interval = args.interval or config.interval_sec
        run_loop(config, interval, run=lambda: run_once(config, sources=sources, publish=not args.no_publish))
        return 0
    now = parse_iso(args.now) if args.now else None
    outcome = run_once(config, now=now, sources=sources, publish=not args.no_publish)
    print(outcome.summary)
    return outcome.exit_code


def cmd_serve(args: argparse.Namespace) -> int:
    from .serve import seed_example, serve_forever

    config = load_config(warn=False)
    if args.seed_example and seed_example(config.public_dir, config.docs_dir / "trends.example.json"):
        log.info("seeded %s from docs/trends.example.json", config.trends_path)
    try:
        serve_forever(config.public_dir, args.port, args.host)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    from .pipeline import run_loop, run_once
    from .serve import serve_in_thread

    config = load_config()
    serve_in_thread(config.public_dir, args.port, args.host)
    try:
        run_loop(config, args.interval or config.interval_sec, run=lambda: run_once(config))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_social(args: argparse.Namespace) -> int:
    from .social import NothingToPost, render_drafts

    config = load_config(warn=False)
    path = Path(args.input) if args.input else config.trends_path
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1
    try:
        sys.stdout.write(render_drafts(snapshot, top=args.top, fmt=args.format))
    except NothingToPost:
        print("nothing to post")
        return 1
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    from .resolve import resolve_channels

    return resolve_channels(load_config(), force=args.force)


def cmd_validate(args: argparse.Namespace) -> int:
    from .resolve import validate_seed

    return validate_seed(load_config(warn=args.online), online=args.online)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seoulpulse", description="Korea-first K-pop trends pipeline")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="one pipeline run -> public/trends.json")
    r.add_argument("--now", help="override the run clock (ISO-8601 UTC)")
    r.add_argument("--sources", help=f"comma-separated subset of {','.join(ALL_SOURCE_IDS)}"
                   " (naver only when NAVER_CLIENT_ID/SECRET are set)")
    r.add_argument("--no-publish", action="store_true", help="score but do not write trends.json")
    r.add_argument("--loop", action="store_true", help="run forever")
    r.add_argument("--interval", type=int, help="loop interval in seconds (default SP_INTERVAL_SEC)")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="serve public/ (127.0.0.1 by default)")
    s.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 to reach it from a phone on the LAN)")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--seed-example", action="store_true", help="copy docs/trends.example.json if trends.json is missing")
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("dev", help="serve (thread) + run --loop")
    d.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 to reach it from a phone on the LAN)")
    d.add_argument("--port", type=int, default=8787)
    d.add_argument("--interval", type=int)
    d.set_defaults(func=cmd_dev)

    so = sub.add_parser("social", help="print X/Threads drafts (manual posting only)")
    so.add_argument("--input", help="path to a trends.json (default public/trends.json)")
    so.add_argument("--top", type=int, default=5)
    so.add_argument("--format", choices=("x", "threads", "both"), default="both")
    so.set_defaults(func=cmd_social)

    rc = sub.add_parser("resolve-channels", help="resolve @handles into data/channels.lock.json")
    rc.add_argument("--force", action="store_true", help="re-resolve every handle")
    rc.set_defaults(func=cmd_resolve)

    v = sub.add_parser("validate-seed", help="static seed checks")
    v.add_argument("--online", action="store_true", help="also verify channel ids and canonical wiki titles")
    v.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
