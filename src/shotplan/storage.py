"""SQLite persistence for run history. Standard library only.

Every run is stored with its full request, plan, validation and trace, so a
quality regression can always be traced back to the exact inputs and prompt.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .domain.errors import StorageError
from .domain.models import PlanResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    request_id    TEXT NOT NULL,
    provider      TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    score         REAL NOT NULL,
    hard_pass     INTEGER NOT NULL,
    repair_count  INTEGER NOT NULL,
    duration_ms   REAL NOT NULL,
    cost_cny      REAL NOT NULL,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
"""


class RunStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(f"cannot initialise database at {self.path}: {exc}") from exc

    def save(self, result: PlanResult) -> str:
        trace, validation = result.trace, result.validation
        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO runs
                       (run_id, created_at, request_id, provider, prompt_version,
                        score, hard_pass, repair_count, duration_ms, cost_cny, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        trace.run_id,
                        trace.created_at,
                        result.request.request_id,
                        trace.provider,
                        trace.prompt_version,
                        validation.score,
                        int(validation.hard_pass),
                        trace.repair_count,
                        trace.total_duration_ms,
                        trace.total_cost_cny,
                        payload,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"cannot save run: {exc}") from exc
        return trace.run_id

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT run_id, created_at, provider, prompt_version, score,
                              hard_pass, repair_count, duration_ms, cost_cny
                       FROM runs ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"cannot read runs: {exc}") from exc
        keys = (
            "run_id", "created_at", "provider", "prompt_version", "score",
            "hard_pass", "repair_count", "duration_ms", "cost_cny",
        )
        return [dict(zip(keys, row, strict=False)) for row in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"cannot read run {run_id}: {exc}") from exc
        return json.loads(row[0]) if row else None

    def summary(self) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT COUNT(*), AVG(score), SUM(hard_pass), AVG(duration_ms),
                              SUM(cost_cny) FROM runs"""
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"cannot summarise runs: {exc}") from exc
        total = row[0] or 0
        return {
            "total_runs": total,
            "avg_score": round(row[1] or 0.0, 4),
            "hard_pass_rate": round((row[2] or 0) / total, 4) if total else 0.0,
            "avg_duration_ms": round(row[3] or 0.0, 2),
            "total_cost_cny": round(row[4] or 0.0, 6),
        }
