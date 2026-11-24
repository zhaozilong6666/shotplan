"""Run the real service unattended for a long stretch: no prompts, no restarts by hand.

The script starts uvicorn with N workers, sends one request every `--interval`
seconds, checks `/api/health` every 30 seconds, and restarts the whole process
tree if it dies or fails three health checks in a row. The database survives
restarts, so at the end `successful requests - rows` is the loss count.

It is deliberately boring: no new rules, no new gate. The point is to leave it
running for 30+ minutes and read the report afterwards.

Usage:
    python -m evals.soak --minutes 30 --workers 1 --mode batch --write
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from evals.benchmark import percentile
from evals.http_load import _stop, free_port, one
from shotplan.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "soak.md"
SNAPSHOT = ROOT / "evals" / "out" / "soak.json"


def keep_awake():
    """Ask Windows not to suspend the machine while the soak runs.

    A 16-minute gap in an earlier run was a system sleep, not a service stall.
    ES_CONTINUOUS | ES_SYSTEM_REQUIRED keeps the system awake; the display may
    still turn off. No-op on POSIX.
    """
    if os.name != "nt":
        return lambda: None
    import ctypes

    continuous = 0x80000000
    system_required = 0x00000001
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(continuous | system_required)
    return lambda: kernel32.SetThreadExecutionState(continuous)


def cleanup_stale_temp_dirs() -> None:
    """Remove soak DBs left behind by a hard-killed run.

    A PID file makes this safe: a directory whose PID is still alive is kept.
    Directories without a PID file are only removed after 12 hours, which is
    longer than the supported single-run ceiling.
    """
    base = Path(tempfile.gettempdir())
    for path in base.glob("shotplan-soak-*"):
        if not path.is_dir():
            continue
        pid_file = path / "soak.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
                continue
            except (OSError, ValueError):
                pass
        elif time.time() - path.stat().st_mtime < 12 * 3600:
            continue
        shutil.rmtree(path, ignore_errors=True)


def start_server(workers: int, db_path: Path, mode: str) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = os.environ.copy()
    env["SHOTPLAN_DB_PATH"] = str(db_path)
    env["SHOTPLAN_PROVIDER"] = "mock"
    env["SHOTPLAN_PERSIST_MODE"] = mode
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable, "-m", "uvicorn", "shotplan.api:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--workers", str(workers), "--log-level", "warning", "--no-access-log",
    ]
    kwargs: dict[str, Any] = {
        "env": env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)  # noqa: S603 - fixed, local command
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as probe:
                if probe.get(base_url + "/api/health").status_code == 200:
                    return proc, base_url
        except Exception:  # noqa: BLE001 - readiness polling
            time.sleep(0.3)
    _stop(proc)
    raise RuntimeError("uvicorn did not become ready in 30s")


def run(
    minutes: float,
    workers: int,
    mode: str,
    interval: float,
    kill_at: float = 0.0,
    concurrency: int = 1,
    status_path: Path | None = None,
    live_path: Path | None = None,
) -> dict[str, Any]:
    release_awake = keep_awake()
    try:
        return _run(
            minutes,
            workers,
            mode,
            interval,
            kill_at,
            concurrency,
            status_path,
            live_path,
        )
    finally:
        release_awake()


def _run(
    minutes: float,
    workers: int,
    mode: str,
    interval: float,
    kill_at: float = 0.0,
    concurrency: int = 1,
    status_path: Path | None = None,
    live_path: Path | None = None,
) -> dict[str, Any]:
    cleanup_stale_temp_dirs()
    with tempfile_dir() as tmp:
        db_path = Path(tmp) / "soak.db"
        (db_path.parent / "soak.pid").write_text(str(os.getpid()), encoding="utf-8")
        proc, base_url = start_server(workers, db_path, mode)
        client = httpx.Client(base_url=base_url, timeout=30.0, trust_env=False)
        started = time.monotonic()
        deadline = started + minutes * 60
        index = 0
        ok = 0
        errors: list[str] = []
        latencies: list[float] = []
        restarts = 0
        chaos_kills = 0
        chaos_done = False
        health_failures = 0
        samples: list[dict[str, Any]] = []
        last_health = started
        lock = threading.Lock()

        def publish(final: bool = False) -> None:
            if not status_path and not live_path:
                return
            partial = {
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "minutes": round((time.monotonic() - started) / 60, 2),
                "mode": mode,
                "workers": workers,
                "concurrency": concurrency,
                "requests": index,
                "ok": ok,
                "errors": len(errors),
                "restarts": restarts,
                "rows": RunStore(db_path).count(),
                "samples": samples[-12:],
                "final": final,
            }
            if status_path:
                status_path.parent.mkdir(parents=True, exist_ok=True)
                status_path.write_text(
                    json.dumps(partial, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if live_path:
                lines = [
                    "# 长跑实时状态",
                    "",
                    f"- 更新时间：{partial['updated_at']}",
                    f"- 已运行：{partial['minutes']:.1f} 分钟",
                    f"- 配置：{workers} worker / {mode} / 并发 {concurrency}",
                    f"- 请求 / 成功 / 错误 / 重启：{partial['requests']} / "
                    f"{partial['ok']} / {partial['errors']} / {partial['restarts']}",
                    f"- 落库：{partial['rows']}",
                    "",
                ]
                if samples:
                    lines += [
                        "| 分钟 | 请求 | 成功 | 错误 | 重启 | 行数 |",
                        "| ---: | ---: | ---: | ---: | ---: | ---: |",
                    ]
                    for item in samples[-8:]:
                        lines.append(
                            f"| {item['elapsed_min']} | {item['requests']} | {item['ok']} "
                            f"| {item['errors']} | {item['restarts']} | {item['rows']} |"
                        )
                live_path.parent.mkdir(parents=True, exist_ok=True)
                live_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        def next_index() -> int:
            nonlocal index
            with lock:
                value = index
                index += 1
                return value

        def record(result) -> None:
            nonlocal ok
            with lock:
                latencies.append(result.elapsed_ms)
                if result.kind == "ok":
                    ok += 1
                else:
                    errors.append(result.detail)

        def restart() -> None:
            nonlocal proc, base_url, client, restarts, health_failures
            restarts += 1
            _stop(proc)
            try:
                client.close()
            except Exception:  # noqa: BLE001 - old client is gone
                pass
            proc, base_url = start_server(workers, db_path, mode)
            client = httpx.Client(base_url=base_url, timeout=30.0, trust_env=False)
            health_failures = 0

        def traffic_round(round_end: float) -> None:
            while time.monotonic() < round_end:
                item = next_index()
                result = one(client, item)
                record(result)
                if interval > 0:
                    time.sleep(interval)

        try:
            while time.monotonic() < deadline:
                elapsed_now = time.monotonic() - started
                if kill_at and not chaos_done and elapsed_now >= kill_at * 60:
                    chaos_done = True
                    chaos_kills += 1
                    print(
                        f"chaos: hard-killing the service at {elapsed_now / 60:.1f}m",
                        flush=True,
                    )
                    _stop(proc)
                    restart()
                    continue
                if proc.poll() is not None:
                    restart()
                if concurrency <= 1:
                    result = one(client, next_index())
                    record(result)
                    if interval > 0:
                        time.sleep(interval)
                else:
                    round_end = min(time.monotonic() + 30, deadline)
                    threads = [
                        threading.Thread(target=traffic_round, args=(round_end,))
                        for _ in range(concurrency)
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
                now = time.monotonic()
                if now - last_health >= 30 or concurrency > 1:
                    healthy = False
                    try:
                        healthy = client.get("/api/health", timeout=5).status_code == 200
                    except Exception:  # noqa: BLE001 - health is a probe
                        healthy = False
                    if not healthy:
                        health_failures += 1
                    if proc.poll() is not None or health_failures >= 3:
                        restart()
                    rows = RunStore(db_path).count()
                    samples.append(
                        {
                            "elapsed_min": round((now - started) / 60, 1),
                            "requests": index,
                            "ok": ok,
                            "errors": len(errors),
                            "restarts": restarts,
                            "rows": rows,
                        }
                    )
                    print(
                        f"{samples[-1]['elapsed_min']:.1f}m req={index} ok={ok} "
                        f"err={len(errors)} restarts={restarts} rows={rows}",
                        flush=True,
                    )
                    publish()
                    last_health = now
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - shutdown path
                pass
            _stop(proc)
        elapsed = time.monotonic() - started
        rows = RunStore(db_path).summary()["total_runs"]
        p99 = percentile(latencies, 99) if latencies else 0.0
        p999 = percentile(latencies, 99.9) if latencies else 0.0
        publish(final=True)
        return {
            "minutes": round(elapsed / 60, 2),
            "workers": workers,
            "mode": mode,
            "interval_s": interval,
            "concurrency": concurrency,
            "requests": index,
            "ok": ok,
            "errors": len(errors),
            "error_samples": errors[:5],
            "restarts": restarts,
            "chaos_kills": chaos_kills,
            "rows": rows,
            "lost": max(0, ok - rows),
            "rps": round(index / elapsed, 2) if elapsed else 0.0,
            "p50_ms": round(percentile(latencies, 50), 2) if latencies else 0.0,
            "p95_ms": round(percentile(latencies, 95), 2) if latencies else 0.0,
            # Rare 30s stalls are invisible in P95. Keep the tail explicit so a
            # "0 errors" claim cannot hide a 1-in-7000 freeze.
            "p99_ms": round(p99, 2),
            "p999_ms": round(p999, 2),
            "max_ms": round(max(latencies), 2) if latencies else 0.0,
            "slow_1s": sum(1 for value in latencies if value > 1000),
            "slow_5s": sum(1 for value in latencies if value > 5000),
            "samples": samples,
        }


class tempfile_dir:
    def __enter__(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(
            prefix="shotplan-soak-", ignore_cleanup_errors=True
        )
        return self._tmp.name

    def __exit__(self, *exc):
        self._tmp.cleanup()


def report(result: dict[str, Any]) -> str:
    lines = [
        "> 由 `python -m evals.soak --write` 生成本机报告，请勿手工编辑。",
        "",
        "# 无人值守长跑",
        "",
        f"{result['minutes']:.1f} 分钟，{result['workers']} worker，"
        f"`{result['mode']}` 持久化，并发 {result.get('concurrency', 1)}，"
        f"每 {result['interval_s']}s 一个请求；"
        "每 30s 探活，连续失败 3 次或进程退出则重启。",
        "",
        "## 结果",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        f"| 请求 | {result['requests']} |",
        f"| 成功 | {result['ok']} |",
        f"| 错误 | {result['errors']} |",
        f"| 并发 | {result.get('concurrency', 1)} |",
        f"| 重启 | {result['restarts']} |",
        f"| 人为杀进程 | {result.get('chaos_kills', 0)} |",
        f"| 落库 | {result['rows']} |",
        f"| 丢写 | {result['lost']} |",
        f"| 吞吐 | {result['rps']} /s |",
        f"| P50 / P95 | {result['p50_ms']} / {result['p95_ms']} ms |",
        f"| P99 / P99.9 / 最大 | {result['p99_ms']} / {result['p999_ms']} / "
        f"{result['max_ms']} ms |",
        f"| 慢请求 >1s / >5s | {result['slow_1s']} / {result['slow_5s']} |",
        "",
    ]
    if result["samples"]:
        lines += [
            "## 时间线",
            "",
            "| 分钟 | 请求 | 成功 | 错误 | 重启 | 行数 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sample in result["samples"]:
            lines.append(
                f"| {sample['elapsed_min']} | {sample['requests']} | {sample['ok']} "
                f"| {sample['errors']} | {sample['restarts']} | {sample['rows']} |"
            )
        lines.append("")
    if result["error_samples"]:
        lines += ["## 错误样本", ""]
        lines += [f"- `{sample}`" for sample in result["error_samples"]]
        lines.append("")
    lines += [
        "## 边界",
        "",
        f"mock provider、单机 loopback；并发 {result.get('concurrency', 1)}，"
        f"每个 worker 每 {result['interval_s']}s 一个请求。这是稳定性长跑，不是容量压测；"
        "错误样本与重启原因都在 JSON 快照里，不掩盖失败。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="unattended soak run")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--mode", choices=["off", "sync", "async", "batch"], default="batch")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--kill-at-minutes", type=float, default=0.0,
        help="hard-kill the service once at this minute to test unattended restart",
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument(
        "--status",
        type=Path,
        default=ROOT / "evals" / "out" / "soak-status.json",
        help="machine-readable live status, rewritten every sample",
    )
    parser.add_argument(
        "--live",
        type=Path,
        default=ROOT / "evals" / "out" / "soak-live.md",
        help="human-readable live status, rewritten every sample",
    )
    args = parser.parse_args(argv)
    result = run(
        args.minutes,
        args.workers,
        args.mode,
        args.interval,
        args.kill_at_minutes,
        args.concurrency,
        args.status,
        args.live,
    )
    text = report(result)
    print(text)
    if args.write:
        args.report.write_text(text, encoding="utf-8", newline="\n")
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"report: {args.report}")
    return 0 if result["errors"] == 0 and result["lost"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
