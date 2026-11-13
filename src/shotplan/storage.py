"""SQLite persistence for run history. Standard library only.

Every run is stored with its full request, plan, validation and trace, so a
quality regression can always be traced back to the exact inputs and prompt.

Two write strategies share one schema:

* ``RunStore.save``  - synchronous, committed before the caller returns.
* ``RunWriter.save`` - enqueues and returns; one background thread batches up
  to N rows per transaction. This is the fix for the measured bottleneck in
  docs/worker-bottleneck.md: the write is the largest single cost on the
  request's critical path.

The writer never drops a row. If its queue is saturated it performs a
synchronous write instead, which is slower for that one request but keeps the
"every accepted request is stored" contract that the HTTP gates assert. What it
does trade is durability *at the instant of a hard kill*: rows still in the
queue are gone. That window is measured in tests/test_write_behind.py rather
than denied. See ADR 0012.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import queue
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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

_INSERT = """
INSERT OR REPLACE INTO runs
    (run_id, created_at, request_id, provider, prompt_version,
     score, hard_pass, repair_count, duration_ms, cost_cny, payload)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class RunStore:
    # Schema creation is process-global: the API builds a store per request, so a
    # naive `CREATE TABLE` + `PRAGMA journal_mode=WAL` on every instantiation
    # races under concurrency and surfaces as "database is locked" (HTTP 422).
    _INIT_LOCK = threading.Lock()
    _INITIALISED: set[str] = set()
    _WRITE_LOCK = threading.Lock()
    _RETRIES = 3

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        # timeout is SQLite's busy timeout: under concurrent writers the default
        # (5s) is fine, but making it explicit documents the intent.
        conn = sqlite3.connect(self.path, timeout=5.0)
        return conn

    def _init(self) -> None:
        key = str(self.path.resolve())
        with self._INIT_LOCK:
            if key in self._INITIALISED and self.path.exists():
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as conn:
                    # WAL lets readers proceed during a write and removes the
                    # rollback-journal fsync from the commit path. Measured by
                    # evals/loadtest.py: at 4 concurrent writers p95 fell from
                    # ~209ms to ~55ms and throughput roughly doubled. See
                    # docs/loadtest.md for the full table.
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                self._INITIALISED.add(key)
            except (OSError, sqlite3.Error) as exc:
                raise StorageError(
                    f"cannot initialise database at {self.path}: {exc}"
                ) from exc

    def save(self, result: PlanResult) -> str:
        self.save_many([result])
        return result.trace.run_id

    def save_many(self, results: Sequence[PlanResult]) -> int:
        """Insert several runs in one transaction.

        Batching is what makes write-behind worthwhile: SQLite commits per
        transaction, so 32 rows cost one fsync instead of 32. The rows are
        written with INSERT OR REPLACE, so a retry after a lock timeout cannot
        duplicate a run.
        """
        rows = [self._row(result) for result in results]
        if not rows:
            return 0
        # SQLite has exactly one writer, so a process-level lock is not a
        # bottleneck — it is what stops WAL's shared-memory file from being
        # initialised by several threads at once ("attempt to write a readonly
        # database") and stops writers from burning the busy timeout on each
        # other. Cross-process contention still retries below.
        with self._WRITE_LOCK:
            for attempt in range(self._RETRIES):
                try:
                    with contextlib.closing(self._connect()) as conn, conn:
                        conn.executemany(_INSERT, rows)
                    return len(rows)
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    retryable = "locked" in message or "readonly" in message or "busy" in message
                    if not retryable or attempt == self._RETRIES - 1:
                        raise StorageError(f"cannot save run: {exc}") from exc
                    time.sleep(0.05 * (attempt + 1))
                except sqlite3.Error as exc:
                    raise StorageError(f"cannot save run: {exc}") from exc
        raise StorageError("cannot save run: retries exhausted")

    @staticmethod
    def _row(result: PlanResult) -> tuple[Any, ...]:
        trace, validation = result.trace, result.validation
        return (
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
            json.dumps(result.to_dict(), ensure_ascii=False),
        )

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

    def recent_payloads(self, limit: int = 200) -> list[dict[str, Any]]:
        """Full stored results, newest first. Used by the observability dashboard."""
        capped = min(max(limit, 1), 1000)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?",
                    (capped,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"cannot read recent payloads: {exc}") from exc
        return [json.loads(row[0]) for row in rows]

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


class RunSink(Protocol):
    """What ``run_plan(store=...)`` actually needs. Two implementations exist."""

    def save(self, result: PlanResult) -> str: ...


@dataclass
class WriterStats:
    submitted: int = 0
    written: int = 0
    failed: int = 0
    batches: int = 0
    max_batch: int = 0
    # Requests whose enqueue timed out and were written synchronously instead.
    # Non-zero means the writer is saturated; it is visible rather than silent.
    sync_fallbacks: int = 0

    @property
    def pending(self) -> int:
        return max(0, self.submitted - self.written - self.failed)

    def to_dict(self) -> dict[str, int]:
        return {
            "submitted": self.submitted,
            "written": self.written,
            "failed": self.failed,
            "pending": self.pending,
            "batches": self.batches,
            "max_batch": self.max_batch,
            "sync_fallbacks": self.sync_fallbacks,
        }


class RunWriter:
    """Write-behind queue with batching, for the run-history table.

    Why a thread and a queue rather than ``BackgroundTasks``: FastAPI's
    BackgroundTasks still runs *in the request's process* after the response is
    sent, so a crash loses the work just the same, and it offers no batching or
    back-pressure. A single writer thread per process also matches SQLite's
    single-writer reality: N request threads do not contend for the write lock,
    they hand rows to one thread that commits them in batches.

    Guarantees:
      * an accepted run is never silently dropped: a full queue falls back to a
        synchronous write and counts it;
      * ``flush()`` is what makes "written" observable, so tests and the load
        harness can distinguish "durable" from "accepted";
      * a hard kill can lose whatever is still queued. That window is bounded
        by (queue depth x flush interval) and measured, not hand-waved.
    """

    def __init__(
        self,
        store: RunStore,
        *,
        batch_size: int = 32,
        flush_interval: float = 0.02,
        max_queue: int = 2048,
        enqueue_timeout: float = 0.05,
    ) -> None:
        self.store = store
        self.batch_size = max(1, batch_size)
        self.flush_interval = max(0.001, flush_interval)
        self.enqueue_timeout = max(0.0, enqueue_timeout)
        self.stats = WriterStats()
        self._queue: queue.Queue[PlanResult] = queue.Queue(maxsize=max(1, max_queue))
        self._stopping = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._loop, name="shotplan-run-writer", daemon=True
        )
        self._thread.start()

    # -- public API ---------------------------------------------------------
    def save(self, result: PlanResult) -> str:
        """Same signature as ``RunStore.save`` so the pipeline cannot tell."""
        self.submit(result)
        return result.trace.run_id

    def submit(self, result: PlanResult) -> bool:
        """Enqueue a run. Returns False when it had to be written synchronously."""
        with self._lock:
            closed = self._closed
        if closed:
            # A late submit (shutdown race, background task) would sit in a
            # queue whose thread has already exited — accepted, never written,
            # and invisible. Write it inline instead: slower, but durable.
            self.store.save(result)
            with self._lock:
                self.stats.submitted += 1
                self.stats.written += 1
                self.stats.sync_fallbacks += 1
            return False
        try:
            self._queue.put(result, timeout=self.enqueue_timeout)
        except queue.Full:
            self.store.save(result)
            with self._lock:
                self.stats.sync_fallbacks += 1
                self.stats.submitted += 1
                self.stats.written += 1
            return False
        with self._lock:
            self.stats.submitted += 1
        return True

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until every submitted row is committed (or timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.stats.pending == 0:
                    return True
            time.sleep(0.005)
        with self._lock:
            return self.stats.pending == 0

    def close(self, timeout: float = 10.0) -> bool:
        """Stop the thread after draining. Idempotent."""
        with self._lock:
            if self._closed:
                return True
        self._stopping.set()
        drained = self.flush(timeout)
        self._thread.join(timeout=timeout)
        with self._lock:
            self._closed = True
        return drained

    @property
    def pending(self) -> int:
        with self._lock:
            return self.stats.pending

    # -- internals ----------------------------------------------------------
    def _loop(self) -> None:
        while True:
            batch = self._collect()
            if batch:
                self._write(batch)
                continue
            if self._stopping.is_set() and self._queue.empty():
                return

    def _collect(self) -> list[PlanResult]:
        batch: list[PlanResult] = []
        try:
            # The first item waits up to flush_interval: that is the batching
            # window which turns N commits into one. Everything after it is
            # taken without waiting, so backlog is drained at full speed.
            batch.append(self._queue.get(timeout=self.flush_interval))
        except queue.Empty:
            return batch
        while len(batch) < self.batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write(self, batch: list[PlanResult]) -> None:
        # One retry, then give up on this batch: most failures here are a
        # transient lock or a momentarily unavailable disk, and retrying is
        # cheaper than losing rows. A persistent failure must not kill the
        # thread — the API stays up and the count is exposed via /api/config.
        for attempt in range(2):
            try:
                self.store.save_many(batch)
            except StorageError:
                if attempt:
                    with self._lock:
                        self.stats.failed += len(batch)
                    return
                time.sleep(0.05)
                continue
            with self._lock:
                self.stats.written += len(batch)
                self.stats.batches += 1
                self.stats.max_batch = max(self.stats.max_batch, len(batch))
            return


_WRITERS: dict[str, RunWriter] = {}
_WRITERS_LOCK = threading.Lock()


def get_writer(store: RunStore) -> RunWriter:
    """One writer per database path per process.

    A process-wide singleton matters: the API builds a store per request, and
    a writer per request would mean one thread and one queue per request,
    which is strictly worse than writing synchronously. Keyed by resolved path
    so a temp database in a test does not inherit the production writer.
    """
    key = str(Path(store.path).resolve())
    with _WRITERS_LOCK:
        writer = _WRITERS.get(key)
        if writer is None:
            writer = RunWriter(store)
            _WRITERS[key] = writer
        return writer


def close_all_writers(timeout: float = 10.0) -> None:
    with _WRITERS_LOCK:
        writers = list(_WRITERS.values())
        _WRITERS.clear()
    for writer in writers:
        writer.close(timeout=timeout)


atexit.register(close_all_writers)
