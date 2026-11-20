import sqlite3

import pytest

from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore


def test_store_roundtrip(tmp_path):
    store = RunStore(tmp_path / "nested" / "runs.db")
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    run_id = store.save(result)

    assert store.get(run_id)["trace"]["run_id"] == run_id
    assert store.get("missing") is None
    rows = store.recent()
    assert rows[0]["run_id"] == run_id
    assert rows[0]["hard_pass"] == 1


def test_summary_aggregates(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    for mode in ("good", "naive"):
        store.save(run_plan(PlanRequest(), provider=MockProvider(mode), max_repairs=0))
    summary = store.summary()
    assert summary["total_runs"] == 2
    assert summary["hard_pass_rate"] == 0.5
    assert 0 < summary["avg_score"] < 1
    assert summary["avg_duration_ms"] > 0


def test_save_is_idempotent(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    store.save(result)
    store.save(result)
    assert store.summary()["total_runs"] == 1


def test_recent_respects_limit(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    for i in range(5):
        result = run_plan(PlanRequest(hours=4 + i),
                          provider=MockProvider("good"), max_repairs=0)
        store.save(result)
    assert len(store.recent(2)) == 2


def test_invalid_path_raises_storage_error(tmp_path):
    from shotplan.domain.errors import StorageError

    bad_file = tmp_path / "not_a_dir"
    bad_file.write_text("x", encoding="utf-8")
    with pytest.raises(StorageError):
        RunStore(bad_file / "child" / "runs.db")


def test_concurrent_writes_are_all_stored(tmp_path):
    """Regression: per-request RunStore() raced on WAL init and lost writes.

    Before the process-level init/write locks, 8 threads could produce
    "attempt to write a readonly database" (surfacing as HTTP 422) or simply
    drop rows. Every accepted run must be stored.
    """
    from concurrent.futures import ThreadPoolExecutor

    store = RunStore(tmp_path / "concurrent.db")
    results = [run_plan(PlanRequest(hours=4 + i % 5),
                        provider=MockProvider("good"), max_repairs=0) for i in range(24)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        run_ids = list(pool.map(store.save, results))

    assert len(set(run_ids)) == 24
    assert store.summary()["total_runs"] == 24
    assert all(store.get(run_id) is not None for run_id in run_ids)


def test_schema_version_is_recorded(tmp_path):
    path = tmp_path / "versioned.db"
    RunStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_a_v1_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                request_id TEXT NOT NULL, provider TEXT NOT NULL,
                prompt_version TEXT NOT NULL, score REAL NOT NULL,
                hard_pass INTEGER NOT NULL, repair_count INTEGER NOT NULL,
                duration_ms REAL NOT NULL, cost_cny REAL NOT NULL,
                payload TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
    store = RunStore(path)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "status" in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert store.recent() == []


def test_stats_rows_do_not_include_payload(tmp_path):
    store = RunStore(tmp_path / "stats.db")
    store.save(run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0))
    rows = store.stats_rows()
    assert rows and "payload" not in rows[0]
    assert {"provider", "prompt_version", "status", "score", "duration_ms"} <= set(rows[0])


def test_prune_keeps_the_newest_runs(tmp_path):
    store = RunStore(tmp_path / "prune.db")
    for i in range(5):
        store.save(run_plan(PlanRequest(hours=4 + i), max_repairs=0))
    removed = store.prune(keep_last=2)
    assert removed == 3
    assert store.summary()["total_runs"] == 2


def test_prune_older_than_removes_only_stale_runs(tmp_path):
    store = RunStore(tmp_path / "retention.db")
    stale = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    stale.trace.created_at = "2020-01-01T00:00:00+00:00"
    store.save(stale)
    fresh = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    store.save(fresh)

    assert store.prune_older_than(0) == 0  # 0 means "keep everything"
    assert store.prune_older_than(30) == 1
    assert store.summary()["total_runs"] == 1
    assert store.get(fresh.trace.run_id) is not None
    assert store.get(stale.trace.run_id) is None


def test_a_newer_schema_is_rejected(tmp_path):
    from shotplan.domain.errors import StorageError

    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 999")
    with pytest.raises(StorageError, match="newer schema"):
        RunStore(path)


def test_summary_quality_metrics_ignore_provider_outages(tmp_path):
    from dataclasses import replace

    store = RunStore(tmp_path / "outage.db")
    good = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    store.save(good)
    outage = replace(
        good,
        status="error",
        trace=replace(good.trace, run_id="outage-1", wall_duration_ms=99999.0),
    )
    store.save(outage)

    summary = store.summary()
    assert summary["total_runs"] == 2
    assert summary["error_runs"] == 1
    assert summary["hard_pass_rate"] == 1.0, "the outage must not lower the pass rate"
    assert summary["error_rate"] == 0.5


def test_init_retries_a_transient_readonly_error(tmp_path, monkeypatch):
    """4 uvicorn workers can race schema creation; a transient error must retry."""
    db = tmp_path / "race" / "runs.db"
    store = RunStore.__new__(RunStore)
    store.path = db
    calls = {"n": 0}
    real = RunStore._connect

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("attempt to write a read-only database")
        return real(self)

    monkeypatch.setattr(RunStore, "_connect", flaky)
    store._init()

    assert calls["n"] == 2
    assert db.exists()


def test_init_does_not_retry_a_permanent_error(tmp_path, monkeypatch):
    from shotplan.domain.errors import StorageError

    store = RunStore.__new__(RunStore)
    store.path = tmp_path / "bad" / "runs.db"
    calls = {"n": 0}

    def broken(self):
        calls["n"] += 1
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(RunStore, "_connect", broken)
    with pytest.raises(StorageError):
        store._init()
    assert calls["n"] == 1
