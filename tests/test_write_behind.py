"""Write-behind persistence: does it keep the speed *and* the rows?

The claim under test is not "async is faster" (that is measured in
evals/persist_lab.py). It is the durability contract:

* an accepted run is never silently dropped, even when the queue is full;
* ``flush()`` really is a barrier, so "written" is observable;
* committed rows survive a hard kill that skips every cleanup hook;
* a batch failure does not kill the writer thread and take logging down with it.

The last two need a real process. A thread cannot be killed mid-write from
inside the same interpreter, so the hard-kill case runs in a child that calls
``os._exit``, bypassing atexit and every ``finally`` block.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from shotplan.agents import MockProvider
from shotplan.config import Settings
from shotplan.domain.errors import ConfigError, StorageError
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore, RunWriter, close_all_writers, get_writer

ROOT = Path(__file__).resolve().parents[1]


def _results(count: int):
    return [
        run_plan(PlanRequest(hours=4 + i % 3), provider=MockProvider("good"), max_repairs=0)
        for i in range(count)
    ]


class SlowStore(RunStore):
    """Stands in for an fsync that is slower than the request rate."""

    def __init__(self, path, delay=0.05):
        super().__init__(path)
        self.delay = delay
        self.calls = 0

    def save_many(self, results):
        self.calls += 1
        time.sleep(self.delay)
        return super().save_many(results)


class FlakyStore(RunStore):
    def __init__(self, path, fail_first=1):
        super().__init__(path)
        self.remaining_failures = fail_first

    def save_many(self, results):
        if self.remaining_failures:
            self.remaining_failures -= 1
            raise StorageError("simulated disk failure")
        return super().save_many(results)


def test_writer_batches_instead_of_committing_per_row(tmp_path):
    store = RunStore(tmp_path / "batch.db")
    writer = RunWriter(store, batch_size=32, flush_interval=0.05)
    try:
        for result in _results(120):
            writer.save(result)
        assert writer.flush(timeout=10)
        stats = writer.stats.to_dict()
        assert stats["written"] == 120
        assert stats["pending"] == 0
        # 120 rows must not cost 120 transactions. Well under the naive count.
        assert stats["batches"] <= 20, stats
        assert stats["max_batch"] > 1
        assert store.summary()["total_runs"] == 120
    finally:
        writer.close()


def test_flush_is_a_barrier_not_a_hint(tmp_path):
    store = SlowStore(tmp_path / "barrier.db", delay=0.02)
    writer = RunWriter(store, batch_size=4, flush_interval=0.01)
    try:
        for result in _results(12):
            writer.save(result)
        # Before flush the rows may or may not be there; that is the point.
        assert writer.pending > 0
        assert writer.flush(timeout=10)
        assert writer.pending == 0
        assert store.summary()["total_runs"] == 12
    finally:
        writer.close()


def test_close_drains_and_is_idempotent(tmp_path):
    store = RunStore(tmp_path / "close.db")
    writer = RunWriter(store, batch_size=8)
    for result in _results(20):
        writer.save(result)
    assert writer.close() is True
    assert writer.close() is True
    assert store.summary()["total_runs"] == 20


def test_saturated_queue_falls_back_to_synchronous_writes(tmp_path):
    """Never drop: a full queue degrades latency, not durability."""
    store = SlowStore(tmp_path / "full.db", delay=0.05)
    writer = RunWriter(store, batch_size=1, flush_interval=0.01, max_queue=1,
                       enqueue_timeout=0.001)
    try:
        accepted = [writer.save(result) for result in _results(20)]
        assert writer.stats.sync_fallbacks > 0, "the queue was never saturated"
        assert writer.flush(timeout=20)
        assert store.summary()["total_runs"] == 20
        assert len(set(accepted)) == 20
    finally:
        writer.close()


def test_a_failed_batch_does_not_kill_the_writer(tmp_path):
    # Two consecutive failures exhaust the single retry, so one batch is really
    # abandoned — and is counted rather than silently retried forever.
    store = FlakyStore(tmp_path / "flaky.db", fail_first=2)
    writer = RunWriter(store, batch_size=2, flush_interval=0.01)
    try:
        for result in _results(6):
            writer.save(result)
        assert writer.flush(timeout=5)
        assert writer.stats.failed == 2
        # The thread is still alive: later rows land normally.
        for result in _results(4):
            writer.save(result)
        assert writer.flush(timeout=5)
        assert writer.stats.written == 8
        assert store.summary()["total_runs"] == 8
    finally:
        writer.close()


def test_concurrent_submitters_lose_nothing(tmp_path):
    store = RunStore(tmp_path / "threads.db")
    writer = RunWriter(store, batch_size=16)
    results = _results(200)
    try:
        def push(chunk):
            for result in chunk:
                writer.save(result)

        threads = [
            threading.Thread(target=push, args=(results[i::8],)) for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert writer.flush(timeout=20)
        assert store.summary()["total_runs"] == 200
        assert writer.stats.written == 200
    finally:
        writer.close()


def test_writer_is_a_drop_in_sink_for_the_pipeline(tmp_path):
    store = RunStore(tmp_path / "sink.db")
    writer = RunWriter(store, batch_size=4)
    try:
        result = run_plan(PlanRequest(), provider=MockProvider("good"), store=writer)
        assert writer.flush(timeout=5)
        assert store.get(result.trace.run_id) is not None
    finally:
        writer.close()


def test_get_writer_is_one_per_database_path(tmp_path):
    store = RunStore(tmp_path / "singleton.db")
    try:
        first = get_writer(store)
        second = get_writer(RunStore(tmp_path / "singleton.db"))
        assert first is second
    finally:
        close_all_writers()


def test_persist_mode_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTPLAN_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("SHOTPLAN_PERSIST_RUNS", raising=False)
    monkeypatch.setenv("SHOTPLAN_PERSIST_MODE", "async")
    assert Settings.from_env().persist_mode == "async"
    assert Settings.from_env().persist_runs is True

    # v0.9.0 boolean switch keeps working.
    monkeypatch.delenv("SHOTPLAN_PERSIST_MODE")
    monkeypatch.setenv("SHOTPLAN_PERSIST_RUNS", "0")
    assert Settings.from_env().persist_mode == "off"
    assert Settings.from_env().persist_runs is False

    monkeypatch.setenv("SHOTPLAN_PERSIST_MODE", "sometimes")
    with pytest.raises(ConfigError):
        Settings.from_env()


# --- the part a thread cannot test: a hard kill ------------------------------

_CHILD = r'''
import json, os, sys, time

sys.path.insert(0, sys.argv[1])
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore, RunWriter

db, count, mode = sys.argv[2], int(sys.argv[3]), sys.argv[4]
store = RunStore(db)

if mode == "abandon":
    # Simulate an fsync slower than the request rate: the queue fills up and
    # the process dies before draining it. This is the loss window.
    original = store.save_many
    def slow(results):
        time.sleep(0.5)
        return original(results)
    store.save_many = slow

writer = RunWriter(store, batch_size=8, flush_interval=5.0, max_queue=4096)
for i in range(count):
    writer.save(run_plan(PlanRequest(hours=4 + i % 3),
                         provider=MockProvider("good"), max_repairs=0))

if mode == "flush":
    writer.flush(timeout=60)

payload = {"in_db": store.summary()["total_runs"], "pending": writer.pending}
print(json.dumps(payload))
sys.stdout.flush()
os._exit(0)   # no atexit, no close(), no finally
'''


def _run_child(tmp_path: Path, count: int, mode: str) -> dict:
    script = tmp_path / "writer_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(  # noqa: S603 - fixed, local interpreter
        [sys.executable, str(script), str(ROOT / "src"), str(tmp_path / f"{mode}.db"),
         str(count), mode],
        capture_output=True, text=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_committed_rows_survive_a_hard_kill(tmp_path):
    count = 40
    child = _run_child(tmp_path, count, "flush")
    assert child["pending"] == 0
    assert child["in_db"] == count
    # Independent read from the parent process: the data is really on disk.
    assert RunStore(tmp_path / "flush.db").summary()["total_runs"] == count


def test_the_loss_window_is_bounded_and_measured(tmp_path):
    """The honest half of the contract: what a hard kill actually costs.

    The writer is deliberately slowed down so the queue cannot drain. The
    point is not "no loss" — it is that the loss is *counted* (pending) and
    that nothing already committed is corrupted.
    """
    count = 40
    child = _run_child(tmp_path, count, "abandon")
    in_db = RunStore(tmp_path / "abandon.db").summary()["total_runs"]
    assert in_db == child["in_db"]
    assert in_db < count, "the test did not actually catch the writer mid-queue"
    assert child["pending"] == count - in_db
    # And every row that did land is intact and readable.
    store = RunStore(tmp_path / "abandon.db")
    assert all(store.get(row["run_id"]) for row in store.recent(100))


def test_a_submit_after_close_is_written_inline_not_lost(tmp_path):
    """The shutdown race: a late submit must not vanish into a dead queue."""
    store = RunStore(tmp_path / "late.db")
    writer = RunWriter(store, batch_size=4)
    writer.close()
    late = _results(1)[0]
    assert writer.submit(late) is False
    assert store.get(late.trace.run_id) is not None
    assert writer.stats.sync_fallbacks == 1
    assert writer.pending == 0
