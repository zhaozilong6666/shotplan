"""Probe whether uvicorn's shared-socket multi-process mode causes the 30s tail.

Starts N single-worker uvicorn processes on N ports, then round-robins the same
load across them. If this is clean while `uvicorn --workers N` tails, the
shared accept socket (not SQLite, middleware, or the app) is the culprit.

    python -m evals.independent_probe --minutes 10 --mode off
    python -m evals.independent_probe --minutes 10 --mode batch --report docs/...
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path

import httpx

from evals.soak import _stop, one, start_server


class RoundRobin:
    def __init__(self, urls: list[str], keepalive: bool = False) -> None:
        limits = None if keepalive else httpx.Limits(max_keepalive_connections=0)
        self.clients = [
            httpx.Client(base_url=url, timeout=30.0, trust_env=False, limits=limits)
            for url in urls
        ]
        self._lock = threading.Lock()
        self._next = 0

    def _pick(self) -> httpx.Client:
        with self._lock:
            client = self.clients[self._next % len(self.clients)]
            self._next += 1
            return client

    def post(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        return self._pick().post(url, **kwargs)

    def get(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        return self._pick().get(url, **kwargs)

    def close(self) -> None:
        for client in self.clients:
            client.close()


def run(minutes: float, mode: str, concurrency: int, interval: float, report: Path | None) -> int:
    procs = []
    urls = []
    db_path = Path(tempfile.gettempdir()) / f"shotplan-independent-{os.getpid()}.db"
    try:
        for _ in range(4):
            proc, url = start_server(1, db_path, mode)
            procs.append(proc)
            urls.append(url)
        client = RoundRobin(urls)
        started = time.monotonic()
        deadline = started + minutes * 60
        lock = threading.Lock()
        latencies: list[float] = []
        errors: list[str] = []
        count = 0

        def traffic() -> None:
            nonlocal count
            while time.monotonic() < deadline:
                with lock:
                    count += 1
                result = one(client, count)
                with lock:
                    latencies.append(result.elapsed_ms)
                    if result.kind != "ok":
                        errors.append(result.detail)
                if interval > 0:
                    time.sleep(interval)

        threads = [threading.Thread(target=traffic) for _ in range(concurrency)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rows = sum(proc.poll() is None for proc in procs)
        ordered = sorted(latencies)
        p95 = ordered[int(0.95 * (len(ordered) - 1))] if ordered else 0
        p999 = ordered[int(0.999 * (len(ordered) - 1))] if ordered else 0
        summary = {
            "minutes": round((time.monotonic() - started) / 60, 2),
            "mode": mode,
            "workers": 4,
            "independent_ports": True,
            "requests": count,
            "errors": len(errors),
            "error_samples": errors[:5],
            "p50_ms": round(statistics.median(ordered), 2) if ordered else 0,
            "p95_ms": round(p95, 2),
            "p999_ms": round(p999, 2),
            "max_ms": round(max(ordered), 2) if ordered else 0,
            "alive_at_end": rows,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                "# Independent-port probe\n\n```json\n"
                + json.dumps(summary, ensure_ascii=False, indent=2)
                + "\n```\n",
                encoding="utf-8",
            )
        return 0 if not errors else 1
    finally:
        for proc in procs:
            _stop(proc)
        try:
            db_path.unlink(missing_ok=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--mode", default="off")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    return run(args.minutes, args.mode, args.concurrency, args.interval, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
