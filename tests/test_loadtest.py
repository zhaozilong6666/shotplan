"""Tests for the concurrency load test.

Absolute throughput is machine-dependent, so these assert the invariants the
gate relies on: every request answered, nothing lost, and a deliberately broken
expectation is detected.
"""

from __future__ import annotations

import asyncio

import pytest
from evals.loadtest import Level, check, percentile, run_pipeline_level

from shotplan.storage import RunStore

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


def test_percentile_basics():
    assert percentile([], 95) == 0.0
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0


def test_pipeline_level_answers_everything_and_stores_it(tmp_path):
    store = RunStore(tmp_path / "load.db")
    level = run_pipeline_level(concurrency=3, requests=8, store=store)
    assert level.errors == []
    assert sum(level.statuses.values()) == 8
    assert len(level.latencies_ms) == 8
    assert store.summary()["total_runs"] == 8


def test_api_level_answers_everything(tmp_path, monkeypatch):
    from evals.loadtest import run_api_level

    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "api-load.db"))
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    level = asyncio.run(run_api_level(concurrency=2, requests=4))
    assert level.errors == []
    assert sum(level.statuses.values()) == 4
    assert store_rows(tmp_path / "api-load.db") == 4


def store_rows(path) -> int:
    return RunStore(path).summary()["total_runs"]


def test_gate_detects_lost_writes():
    level = Level("pipeline", 1, 4, statuses={"ok": 4}, latencies_ms=[1.0, 2.0, 3.0, 4.0])
    assert check([level], expected_rows=4, actual_rows=4) == []
    problems = check([level], expected_rows=4, actual_rows=3)
    assert any("lost writes" in p for p in problems)


def test_gate_detects_errors_and_missing_answers():
    level = Level("api", 2, 4, latencies_ms=[1.0])
    level.errors.append("error:HTTP 500")
    level.statuses["ok"] = 3
    problems = check([level], expected_rows=4, actual_rows=4)
    assert any("error(s)" in p for p in problems)
    assert any("answered" in p for p in problems)
