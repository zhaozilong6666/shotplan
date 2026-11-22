"""Run the real service unattended for a long stretch: no prompts, no restarts by hand.

The script starts uvicorn with N workers, sends one request every `--interval`
seconds, checks `/api/health` every 30 seconds, and restarts the whole process
tree if it dies or fails three health checks in a row. The database survives
restarts, so at the end `successful requests - rows` is the loss count.

It is deliberately boring: no new rules, no new gate. The point is to leave it
running for 30+ minutes and read the report afterwards.

Usage:
    python -m evals.soak --minutes 30 --workers 4 --mode batch --write
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from evals.benchmark import percentile
from evals.http_load import _stop, free_port, one
from shotplan.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "soak.md"
SNAPSHOT = ROOT / "evals" / "out" / "soak.json"


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


def run(minutes: float, workers: int, mode: str, interval: float) -> dict[str, Any]:
    with tempfile_dir() as tmp:
        db_path = Path(tmp) / "soak.db"
        proc, base_url = start_server(workers, db_path, mode)
        client = httpx.Client(base_url=base_url, timeout=30.0, trust_env=False)
        started = time.monotonic()
        deadline = started + minutes * 60
        index = 0
        ok = 0
        errors: list[str] = []
        latencies: list[float] = []
        restarts = 0
        health_failures = 0
        samples: list[dict[str, Any]] = []
        last_health = started
        try:
            while time.monotonic() < deadline:
                result = one(client, index)
                index += 1
                latencies.append(result.elapsed_ms)
                if result.kind == "ok":
                    ok += 1
                else:
                    errors.append(result.detail)
                now = time.monotonic()
                if now - last_health >= 30:
                    healthy = False
                    try:
                        healthy = client.get("/api/health", timeout=5).status_code == 200
                    except Exception:  # noqa: BLE001 - health is a probe
                        healthy = False
                    if not healthy:
                        health_failures += 1
                    if proc.poll() is not None or health_failures >= 3:
                        restarts += 1
                        _stop(proc)
                        try:
                            client.close()
                        except Exception:  # noqa: BLE001 - old client is gone
                            pass
                        proc, base_url = start_server(workers, db_path, mode)
                        client = httpx.Client(
                            base_url=base_url, timeout=30.0, trust_env=False
                        )
                        health_failures = 0
                    rows = RunStore(db_path).summary()["total_runs"]
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
                    last_health = now
                time.sleep(interval)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - shutdown path
                pass
            _stop(proc)
        elapsed = time.monotonic() - started
        rows = RunStore(db_path).summary()["total_runs"]
        return {
            "minutes": round(elapsed / 60, 2),
            "workers": workers,
            "mode": mode,
            "interval_s": interval,
            "requests": index,
            "ok": ok,
            "errors": len(errors),
            "error_samples": errors[:5],
            "restarts": restarts,
            "rows": rows,
            "lost": max(0, ok - rows),
            "rps": round(index / elapsed, 2) if elapsed else 0.0,
            "p50_ms": round(percentile(latencies, 50), 2) if latencies else 0.0,
            "p95_ms": round(percentile(latencies, 95), 2) if latencies else 0.0,
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
        f"`{result['mode']}` 持久化，每 {result['interval_s']}s 一个请求；"
        "每 30s 探活，连续失败 3 次或进程退出则重启。",
        "",
        "## 结果",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        f"| 请求 | {result['requests']} |",
        f"| 成功 | {result['ok']} |",
        f"| 错误 | {result['errors']} |",
        f"| 重启 | {result['restarts']} |",
        f"| 落库 | {result['rows']} |",
        f"| 丢写 | {result['lost']} |",
        f"| 吞吐 | {result['rps']} /s |",
        f"| P50 / P95 | {result['p50_ms']} / {result['p95_ms']} ms |",
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
    lines += [
        "## 边界",
        "",
        "mock provider、单机 loopback、固定 1 请求/间隔；这是稳定性长跑，不是容量压测。"
        "错误样本与重启原因都在 JSON 快照里，不掩盖失败。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="unattended soak run")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mode", choices=["off", "sync", "async", "batch"], default="batch")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    result = run(args.minutes, args.workers, args.mode, args.interval)
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
