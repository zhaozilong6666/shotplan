"""Validate N independent uvicorn ports behind the small local edge proxy."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

from evals.soak import _stop, free_port, one, start_server


def run(minutes: float, mode: str, concurrency: int, interval: float, report: Path | None) -> int:
    procs: list[subprocess.Popen] = []
    backend_urls: list[str] = []
    edge: subprocess.Popen | None = None
    db_path = Path(tempfile.gettempdir()) / f"shotplan-proxy-{os.getpid()}.db"
    try:
        for _ in range(4):
            proc, url = start_server(1, db_path, mode)
            procs.append(proc)
            backend_urls.append(url)
        backends = ",".join(url.split("://", 1)[1] for url in backend_urls)
        port = free_port()
        edge = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "edge_proxy.py"),
             "--backends", backends, "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            timeout=30.0, trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("edge proxy did not become ready")

        started = time.monotonic()
        stop = started + minutes * 60
        lock = threading.Lock()
        latencies: list[float] = []
        errors: list[str] = []
        count = 0

        def traffic() -> None:
            nonlocal count
            while time.monotonic() < stop:
                with lock:
                    count += 1
                    item = count
                result = one(client, item)
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
        ordered = sorted(latencies)
        summary = {
            "minutes": round((time.monotonic() - started) / 60, 2),
            "mode": mode,
            "backends": 4,
            "edge_proxy": "asyncio",
            "requests": count,
            "errors": len(errors),
            "error_samples": errors[:5],
            "p50_ms": round(statistics.median(ordered), 2) if ordered else 0,
            "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 2) if ordered else 0,
            "p999_ms": round(ordered[int(0.999 * (len(ordered) - 1))], 2) if ordered else 0,
            "max_ms": round(max(ordered), 2) if ordered else 0,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                "# Edge-proxy probe\n\n```json\n"
                + json.dumps(summary, ensure_ascii=False, indent=2)
                + "\n```\n",
                encoding="utf-8",
            )
        return 0 if not errors else 1
    finally:
        if edge is not None:
            _stop(edge)
        for proc in procs:
            _stop(proc)
        try:
            db_path.unlink(missing_ok=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--mode", default="batch")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    return run(args.minutes, args.mode, args.concurrency, args.interval, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
