"""Environment-driven configuration. Standard library only.

Configuration is intentionally flat and boring: every value has a safe default
so the whole project runs offline on a fresh clone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .domain.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
PERSIST_MODES = ("off", "sync", "async")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "1" if default else "0").lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean (1/0/true/false)")


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 30
    db_path: Path = ROOT / "data" / "shotplan.db"
    max_repairs: int = 1
    eval_min_score: float = 0.80
    log_level: str = "INFO"
    # off | sync | async. `sync` stays the default because the project promises
    # "every accepted request is stored" and that promise must not silently
    # become "mostly stored". `async` moves the write off the request path and
    # is measurably faster; it is opt-in because it trades a bounded, measured
    # loss window on a hard kill. See ADR 0012 and docs/persistence.md.
    persist_mode: str = "sync"
    # Wall-clock ceiling for one plan request, repairs included. 0 disables it.
    pipeline_budget_s: float = 60.0

    @property
    def persist_runs(self) -> bool:
        """Back-compatible view of the v0.9.0 boolean switch."""
        return self.persist_mode != "off"

    @classmethod
    def from_env(cls) -> Settings:
        raw_db = _env("SHOTPLAN_DB_PATH", str(ROOT / "data" / "shotplan.db"))
        provider = _env("SHOTPLAN_PROVIDER", "mock").lower()
        if provider not in ("mock", "openai_compatible"):
            raise ConfigError(f"unknown SHOTPLAN_PROVIDER: {provider}")
        raw_mode = _env("SHOTPLAN_PERSIST_MODE", "").lower()
        if not raw_mode:
            # v0.9.0 shipped a boolean switch; keep it working rather than
            # breaking every script and doc that used it.
            raw_mode = "sync" if _env_bool("SHOTPLAN_PERSIST_RUNS", True) else "off"
        if raw_mode not in PERSIST_MODES:
            raise ConfigError(
                f"SHOTPLAN_PERSIST_MODE must be one of {PERSIST_MODES}, got {raw_mode!r}"
            )
        return cls(
            provider=provider,
            llm_base_url=_env("SHOTPLAN_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=_env("SHOTPLAN_LLM_API_KEY", ""),
            llm_model=_env("SHOTPLAN_LLM_MODEL", "gpt-4o-mini"),
            llm_timeout=_env_int("SHOTPLAN_LLM_TIMEOUT", 30),
            db_path=Path(raw_db) if Path(raw_db).is_absolute() else ROOT / raw_db,
            max_repairs=max(0, _env_int("SHOTPLAN_MAX_REPAIRS", 1)),
            eval_min_score=_env_float("SHOTPLAN_EVAL_MIN_SCORE", 0.80),
            log_level=_env("SHOTPLAN_LOG_LEVEL", "INFO").upper(),
            persist_mode=raw_mode,
            pipeline_budget_s=max(0.0, _env_float("SHOTPLAN_PIPELINE_BUDGET_S", 60.0)),
        )

    def masked(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "api_key_set": bool(self.llm_api_key),
            "db_path": str(self.db_path),
            "max_repairs": self.max_repairs,
            "eval_min_score": self.eval_min_score,
            "persist_runs": self.persist_runs,
            "persist_mode": self.persist_mode,
            "pipeline_budget_s": self.pipeline_budget_s,
        }


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader, avoids a python-dotenv dependency."""
    target = path or ROOT / ".env"
    if not target.exists():
        return
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_settings() -> Settings:
    load_dotenv()
    return Settings.from_env()
