"""Environment -> Config (spec §1). Relative paths resolve against `backend/`."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger("seoulpulse.config")

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# Used in the User-Agent when SP_CONTACT is unset. Generic on purpose: it names the project and
# tells operators how to fix it. Never put a personal address here.
DEFAULT_CONTACT = "SeoulPulse open-source K-pop trends pipeline; operator: set SP_CONTACT"

DEFAULT_LLM_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class Config:
    backend_dir: Path
    repo_dir: Path
    data_dir: Path
    docs_dir: Path
    db_path: Path
    public_dir: Path
    interval_sec: int
    contact: str
    contact_configured: bool
    top_n: int
    rank_compare_hours: float
    youtube_api_key: str | None
    why_llm: bool
    anthropic_api_key: str | None
    llm_model: str

    @property
    def var_dir(self) -> Path:
        return self.db_path.parent

    @property
    def lock_path(self) -> Path:
        return self.var_dir / "run.lock"

    @property
    def trends_path(self) -> Path:
        return self.public_dir / "trends.json"

    @property
    def user_agent(self) -> str:
        return f"SeoulPulse/0.1 (+{self.contact})"

    def with_overrides(self, **kw) -> Config:
        return replace(self, **kw)


def _path(value: str | None, default: Path, base: Path) -> Path:
    if not value:
        return default
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("ignoring non-integer %s=%r", key, raw)
        return default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("ignoring non-numeric %s=%r", key, raw)
        return default


def load_config(env: Mapping[str, str] | None = None, *, warn: bool = True) -> Config:
    env = os.environ if env is None else env
    contact = env.get("SP_CONTACT", "").strip()
    configured = bool(contact)
    if not configured:
        if warn:
            log.warning(
                "SP_CONTACT is unset; Wikimedia throttles anonymous User-Agents. "
                "Set SP_CONTACT to a contact URL or email."
            )
        contact = DEFAULT_CONTACT
    return Config(
        backend_dir=BACKEND_DIR,
        repo_dir=REPO_DIR,
        data_dir=BACKEND_DIR / "data",
        docs_dir=REPO_DIR / "docs",
        db_path=_path(env.get("SP_DB_PATH"), BACKEND_DIR / "var" / "seoulpulse.db", BACKEND_DIR),
        public_dir=_path(env.get("SP_PUBLIC_DIR"), REPO_DIR / "public", BACKEND_DIR),
        interval_sec=max(60, _int(env, "SP_INTERVAL_SEC", 1800)),
        contact=contact,
        contact_configured=configured,
        top_n=max(1, min(20, _int(env, "SP_TOP_N", 20))),
        rank_compare_hours=max(0.0, _float(env, "SP_RANK_COMPARE_HOURS", 6.0)),
        youtube_api_key=env.get("YOUTUBE_API_KEY", "").strip() or None,
        why_llm=env.get("SP_WHY_LLM", "0").strip() == "1",
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip() or None,
        llm_model=env.get("SP_LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL,
    )
