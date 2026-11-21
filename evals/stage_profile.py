"""Break one request's latency into pipeline stages, HTTP overhead and SQLite.

`http_load` says how many requests per second; it does not say where the time
goes. This lab runs the same brief three ways:

* in-process `run_plan` (pipeline only),
* a real 1-worker uvicorn with `PERSIST_MODE=off` (pipeline + HTTP/JSON),
* the same server with `PERSIST_MODE=sync` (adds the SQLite commit).

The difference between the last two is the write cost; the difference between
the first two is HTTP/JSON. Stage means come from the trace the service already
returns, so nothing is inferred from wall time alone.

Usage:
    python -m evals.stage_profile --write   # 50 requests per arm + report
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from evals.http_load import BRIEFS, server
from shotplan.agents.mock import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "stage-profile.md"
BRIEF = BRIEFS[0]


def _stages(events: list[dict[str, Any]]) -> dict[str, float]:
    return {str(event["name"]): float(event["duration_ms"]) for event in events}


def in_process(rounds: int) -> list[dict[str, Any]]:
    rows = []
    for _ in range(rounds):
        started = time.perf_counter()
        result = run_plan(
            PlanRequest(**BRIEF), provider=MockProvider("good"), store=None
        )
        rows.append(
            {
                "total_ms": (time.perf_counter() - started) * 1000,
                "trace_ms": result.trace.wall_duration_ms,
                "stages": _stages(result.trace.to_dict()["events"]),
            }
        )
    return rows


def http_arm(mode: str, rounds: int) -> list[dict[str, Any]]:
    rows = []
    with tempfile.TemporaryDirectory(
        prefix="shotplan-stage-", ignore_cleanup_errors=True
    ) as tmp:
        with server(1, Path(tmp) / "runs.db", mode=mode) as (base_url, _startup):
            with httpx.Client(base_url=base_url, timeout=60.0, trust_env=False) as client:
                for _ in range(rounds):
                    started = time.perf_counter()
                    response = client.post("/api/plan", json=BRIEF)
                    elapsed = (time.perf_counter() - started) * 1000
                    response.raise_for_status()
                    trace = response.json()["trace"]
                    rows.append(
                        {
                            "total_ms": elapsed,
                            "trace_ms": float(trace["wall_duration_ms"]),
                            "stages": _stages(trace["events"]),
                        }
                    )
    return rows


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stage_means(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for name, duration in row["stages"].items():
            values[name].append(duration)
    return {name: mean(items) for name, items in values.items()}


def report(
    inproc: list[dict[str, Any]], off: list[dict[str, Any]], sync: list[dict[str, Any]]
) -> str:
    def total(rows):
        return mean([row["total_ms"] for row in rows])

    def trace(rows):
        return mean([row["trace_ms"] for row in rows])

    db = total(sync) - total(off)
    http = total(off) - total(inproc)
    names = sorted(
        set(stage_means(inproc)) | set(stage_means(off)) | set(stage_means(sync))
    )
    lines = [
        "> 由 `python -m evals.stage_profile --write` 生成，请勿手工编辑。",
        "",
        "# 单 worker 请求成本拆解",
        "",
        f"同一 brief 各跑 {len(inproc)} 次；mock provider，1 worker，sync/off 各起一次服务。",
        "",
        "## 总量",
        "",
        "| 路径 | 均值 (ms) | 中位数 (ms) | trace wall (ms) | 非 pipeline (ms) |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| in-process | {total(inproc):.2f} | "
        f"{statistics.median([r['total_ms'] for r in inproc]):.2f} | {trace(inproc):.2f} | — |",
        f"| HTTP off | {total(off):.2f} | "
        f"{statistics.median([r['total_ms'] for r in off]):.2f} | {trace(off):.2f} | "
        f"{total(off) - trace(off):.2f} |",
        f"| HTTP sync | {total(sync):.2f} | "
        f"{statistics.median([r['total_ms'] for r in sync]):.2f} | {trace(sync):.2f} | "
        f"{total(sync) - trace(sync):.2f} |",
        "",
        f"- SQLite 同步提交（sync − off）：**{db:.2f} ms/请求**",
        f"- HTTP/JSON 与客户端（off − in-process）：**{http:.2f} ms/请求**",
        f"- pipeline（in-process）：**{total(inproc):.2f} ms/请求**",
        f"- 三部分合计与 sync 的差（噪声）：{total(inproc) + http + db - total(sync):.2f} ms",
        "",
        "## 阶段均值",
        "",
        "| 阶段 | in-process (ms) | HTTP off (ms) | HTTP sync (ms) |",
        "| --- | ---: | ---: | ---: |",
    ]
    a, b, c = stage_means(inproc), stage_means(off), stage_means(sync)
    for name in names:
        lines.append(
            f"| `{name}` | {a.get(name, 0):.2f} | {b.get(name, 0):.2f} | {c.get(name, 0):.2f} |"
        )
    biggest = max(a, key=a.get) if a else "—"
    lines += [
        "",
        "## 结论",
        "",
        f"- 最大的 pipeline 阶段是 `{biggest}`（{a.get(biggest, 0):.2f} ms/请求）。"
        "mock provider 下 `generate` 只有亚毫秒级，别把离线数字当成真实模型延迟。",
        f"- 同步写库占 **{db / total(sync) * 100:.0f}%** 的单请求时间；"
        f"HTTP/JSON 占 **{http / total(sync) * 100:.0f}%**。",
        "- 这只解释「单 worker 为什么慢」，不解释「4 worker 为什么变快」；"
        "后者是多个进程摊薄了同一份单请求成本，见 write-lock-retest.md。",
        "",
        "## 边界",
        "",
        "mock provider、单机 loopback、无 TLS/反代；真实模型请求里 `generate` 会主导，"
        "本表的比例不适用于线上延迟。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="single-request stage profile")
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    inproc = in_process(args.rounds)
    off = http_arm("off", args.rounds)
    sync = http_arm("sync", args.rounds)
    text = report(inproc, off, sync)
    if args.write:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"report: {REPORT}")
    print(text.split("## 总量", 1)[1].split("## 阶段均值", 1)[0].strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
