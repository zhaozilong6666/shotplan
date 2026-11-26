"""Environment-driven configuration. Standard library only.

Configuration is intentionally flat and boring: every value has a safe default
so the whole project runs offline on a fresh clone.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .domain.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
PERSIST_MODES = ("off", "sync", "async", "batch")


def _default_db_path() -> Path:
    """Keep data next to the checkout, but never write into site-packages.

    In an editable/source tree `ROOT` contains pyproject.toml. In a wheel it
    does not, and the old default would point inside the interpreter's library
    directory. Falling back to the working directory keeps installed CLIs usable.
    """
    if (ROOT / "pyproject.toml").exists():
        return ROOT / "data" / "shotplan.db"
    return Path.cwd() / "data" / "shotplan.db"


_DOTENV: dict[str, str] = {}


def _env(name: str, default: str) -> str:
    # A real environment variable wins over `.env`; `.env` stays in this module
    # instead of being exported, so a child process cannot inherit a key just
    # because the parent loaded a file. See load_dotenv().
    if name in os.environ:
        return os.environ[name].strip()
    return _DOTENV.get(name, default).strip()


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
    # Attempts per request. A flaky relay can return 502 in bursts, where the
    # default 3 attempts all land inside the same bad second.
    llm_attempts: int = 3
    # Which system prompt to send. Empty means "the current default", so a
    # deployment does not pin itself to an old prompt by accident.
    prompt_version: str = ""
    # Prospective holdout (docs/router-validation.md) showed no gain:
    # +0.0007 overall, +0.0025 on light-sensitive cases. Keep off.
    prompt_router: bool = False
    # Cost levers. All default to "unset" so behaviour is unchanged unless a
    # deployment opts in; the eval suite sets them explicitly when measuring.
    llm_max_tokens: int = 0  # 0 = let the provider decide
    llm_temperature: float = 0.2
    llm_reasoning_effort: str = ""  # "", low | medium | high (provider-dependent)
    # Estimate an output budget from the plan size, and only raise it when the
    # answer was actually truncated. One extra call in the worst case, several
    # thousand saved output tokens in the usual one.
    llm_adaptive_budget: bool = False
    db_path: Path = field(default_factory=_default_db_path)
    max_repairs: int = 1
    eval_min_score: float = 0.80
    log_level: str = "INFO"
    # off | sync | async | batch. `sync` stays the default because the project promises
    # "every accepted request is stored" and that promise must not silently
    # become "mostly stored". `async` moves the write off the request path and
    # is measurably faster; it is opt-in because it trades a bounded, measured
    # loss window on a hard kill. `batch` keeps the durable promise but shares
    # one commit across concurrent requests (see docs/batch-write.md).
    persist_mode: str = "sync"
    # Optional single-writer sidecar. When set, all finished runs are POSTed to
    # this one process instead of being written by each API worker; it is the
    # measured fix for multi-process SQLite write contention.
    writer_url: str = ""
    # Wall-clock ceiling for one plan request, repairs included. 0 disables it.
    # Must exceed the provider's P99, not its P95, and that P99 depends on load,
    # not only on the model. Measured on deepseek-v4.1-flash, same two cases:
    #   low reasoning, concurrency 1: ~57s per call -> 120s passes
    #   low reasoning, concurrency 6: P95 105s, and two calls pinned at 120.4s
    #   the same two cases at concurrency 2 / 180s budget -> both pass
    # So 120s was measuring the eval harness, not the product: a per-request
    # budget has to cover relay queueing. 180s follows the SLO (P95 <= 180s)
    # and the older default-reasoning regime (P95 119.8s, one case ~160s).
    # Lower it only if you also cap concurrency; the budget itself can be the
    # failure mode.
    pipeline_budget_s: float = 180.0
    # Optional deployment guards. All default to "off" so the offline demo keeps
    # working with no configuration; a public deployment should set at least a
    # key and a rate limit.
    api_key: str = ""
    rate_limit_per_minute: int = 0
    max_body_bytes: int = 1_000_000
    # Delete run history older than N days at startup. 0 keeps everything, which
    # stays the default: silent deletion of an audit trail is a worse surprise
    # than a growing SQLite file.
    retention_days: int = 0

    @property
    def persist_runs(self) -> bool:
        """Back-compatible view of the v0.9.0 boolean switch."""
        return self.persist_mode != "off"

    @classmethod
    def from_env(cls) -> Settings:
        raw_db = _env("SHOTPLAN_DB_PATH", str(_default_db_path()))
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
        effort = _env("SHOTPLAN_LLM_REASONING_EFFORT", "").lower()
        if effort not in ("", "low", "medium", "high"):
            raise ConfigError(
                "SHOTPLAN_LLM_REASONING_EFFORT must be empty, low, medium or high, "
                f"got {effort!r}"
            )
        return cls(
            provider=provider,
            llm_base_url=_env("SHOTPLAN_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=_env("SHOTPLAN_LLM_API_KEY", ""),
            llm_model=_env("SHOTPLAN_LLM_MODEL", "gpt-4o-mini"),
            llm_timeout=_env_int("SHOTPLAN_LLM_TIMEOUT", 30),
            llm_attempts=max(1, _env_int("SHOTPLAN_LLM_ATTEMPTS", 3)),
            prompt_version=_env("SHOTPLAN_PROMPT_VERSION", ""),
            prompt_router=_env_bool("SHOTPLAN_PROMPT_ROUTER", False),
            llm_max_tokens=max(0, _env_int("SHOTPLAN_LLM_MAX_TOKENS", 0)),
            llm_temperature=_env_float("SHOTPLAN_LLM_TEMPERATURE", 0.2),
            llm_reasoning_effort=effort,
            llm_adaptive_budget=_env_bool("SHOTPLAN_LLM_ADAPTIVE_BUDGET", False),
            db_path=Path(raw_db) if Path(raw_db).is_absolute() else ROOT / raw_db,
            max_repairs=max(0, _env_int("SHOTPLAN_MAX_REPAIRS", 1)),
            eval_min_score=_env_float("SHOTPLAN_EVAL_MIN_SCORE", 0.80),
            log_level=_env("SHOTPLAN_LOG_LEVEL", "INFO").upper(),
            persist_mode=raw_mode,
            writer_url=_env("SHOTPLAN_WRITER_URL", "").rstrip("/"),
            pipeline_budget_s=max(0.0, _env_float("SHOTPLAN_PIPELINE_BUDGET_S", 180.0)),
            api_key=_env("SHOTPLAN_API_KEY", ""),
            rate_limit_per_minute=max(0, _env_int("SHOTPLAN_RATE_LIMIT_PER_MINUTE", 0)),
            max_body_bytes=max(1024, _env_int("SHOTPLAN_MAX_BODY_BYTES", 1_000_000)),
            retention_days=max(0, _env_int("SHOTPLAN_RETENTION_DAYS", 0)),
        )

    def resolved(self) -> dict[str, object]:
        """Every effective setting, safe to embed in a snapshot or trace.

        Unlike `masked()` (a small human-facing subset for `/api/config`), this
        is the full reproducibility record. Secrets are replaced by booleans;
        Path values are stringified so the result is JSON-serialisable.
        """
        data = asdict(self)
        data["llm_api_key"] = bool(self.llm_api_key)
        data["api_key"] = bool(self.api_key)
        data["db_path"] = str(self.db_path)
        return {key: data[key] for key in sorted(data)}

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
            "writer_url": self.writer_url,
            "pipeline_budget_s": self.pipeline_budget_s,
            "deploy_api_key_set": bool(self.api_key),
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "max_body_bytes": self.max_body_bytes,
            "retention_days": self.retention_days,
        }


def load_dotenv(path: Path | None = None) -> None:
    """Read `.env` into this module, **not** into `os.environ`.

    Exporting a `.env` value makes every child process inherit it: `http_load`'s
    uvicorn workers, `cli --reload` workers, any subprocess started later. A
    local key then leaks far beyond the file the developer wrote. Keeping the
    values private means each process must load its own `.env` (which the app
    does on startup), and production can inject real environment variables
    without a mounted file.

    `SHOTPLAN_SKIP_DOTENV=1` turns it off. The test suite sets it, because a
    developer's local `.env` (a real provider key, say) must not change what a
    unit test observes: that is how a green local run becomes a red CI run.
    """
    if os.environ.get("SHOTPLAN_SKIP_DOTENV") == "1":
        return
    target = path or ROOT / ".env"
    if not target.exists():
        return
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        _DOTENV.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_settings() -> Settings:
    load_dotenv()
    return Settings.from_env()


def configure_logging(settings: Settings) -> None:
    """Apply SHOTPLAN_LOG_LEVEL. Without this the setting is a dead knob."""
    level = getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
