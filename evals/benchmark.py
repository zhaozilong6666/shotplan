"""Offline performance benchmark: latency percentiles, throughput, memory.

Why this exists: the planner is deterministic and cheap today, and that is
exactly when an accidental O(n^2) or a per-call model reload slips in unnoticed.
A benchmark with absolute ceilings turns "it feels fast" into a gate that fails
on a real regression.

Two deliberate choices:

* **tracemalloc, not RSS.** RSS depends on the interpreter, the OS and whatever
  else the machine is doing; tracemalloc measures the Python heap attributable
  to this loop, so the number is comparable across CI runners.
* **Generous ceilings.** CI runners are noisy. The gate is set to catch an order
  of magnitude, not a 10% jitter. See docs/benchmark.md.

Usage:
    python -m evals.benchmark --check
    python -m evals.benchmark --iterations 500 --write-report
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import OUT_DIR
from evals.run_eval import load_cases
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "benchmark.md"
WARMUP = 5


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile; no interpolation, so it is easy to reason about."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q / 100.0 * len(ordered)) - 1))
    return ordered[index]


@dataclass
class Sample:
    iterations: int
    latencies_ms: list[float] = field(default_factory=list)
    step_ms: dict[str, float] = field(default_factory=dict)
    peak_kb: float = 0.0
    wall_s: float = 0.0

    def metrics(self) -> dict[str, float]:
        return {
            "iterations": self.iterations,
            "p50_ms": round(percentile(self.latencies_ms, 50), 3),
            "p95_ms": round(percentile(self.latencies_ms, 95), 3),
            "p99_ms": round(percentile(self.latencies_ms, 99), 3),
            "mean_ms": round(statistics.fmean(self.latencies_ms), 3) if self.latencies_ms else 0.0,
            "max_ms": round(max(self.latencies_ms, default=0.0), 3),
            "throughput_rps": round(self.iterations / self.wall_s, 2) if self.wall_s else 0.0,
            "peak_kb": round(self.peak_kb, 1),
            "wall_s": round(self.wall_s, 3),
        }


def measure(iterations: int, brief: PlanRequest | None = None) -> Sample:
    request = brief or PlanRequest.from_dict(
        {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 1500, "styles": ["tang"]}
    )
    provider = MockProvider("good")
    for _ in range(WARMUP):
        run_plan(request, provider=provider, max_repairs=1)

    sample = Sample(iterations=iterations)

    # Phase 1: latency and throughput WITHOUT tracemalloc. Tracemalloc hooks
    # every allocation and inflates wall time several fold, so mixing the two
    # would report memory overhead as if it were planning latency.
    total_started = time.perf_counter()
    for _ in range(iterations):
        started = time.perf_counter()
        result = run_plan(request, provider=provider, max_repairs=1)
        sample.latencies_ms.append((time.perf_counter() - started) * 1000)
        for event in result.trace.events:
            sample.step_ms[event.name] = sample.step_ms.get(event.name, 0.0) + event.duration_ms
    sample.wall_s = time.perf_counter() - total_started

    # Phase 2: peak Python heap, measured in its own pass.
    memory_runs = max(10, min(iterations, 100))
    tracemalloc.start()
    tracemalloc.reset_peak()
    for _ in range(memory_runs):
        run_plan(request, provider=provider, max_repairs=1)
    sample.peak_kb = tracemalloc.get_traced_memory()[1] / 1024
    tracemalloc.stop()
    return sample


def sweep() -> dict[str, Any]:
    """Run every eval case once: real-world mix, not one repeated brief."""
    provider = MockProvider("good")
    cases = load_cases()
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    started = time.perf_counter()
    for case in cases:
        request = PlanRequest.from_dict(case["request"])
        tick = time.perf_counter()
        result = run_plan(request, provider=provider, max_repairs=1)
        latencies.append((time.perf_counter() - tick) * 1000)
        statuses[result.status] = statuses.get(result.status, 0) + 1
    wall = time.perf_counter() - started
    return {
        "cases": len(cases),
        "status_counts": dict(sorted(statuses.items())),
        "p50_ms": round(percentile(latencies, 50), 3),
        "p95_ms": round(percentile(latencies, 95), 3),
        "mean_ms": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "max_ms": round(max(latencies, default=0.0), 3),
        "wall_s": round(wall, 3),
    }


def check(
    single: dict[str, float],
    corpus: dict[str, Any],
    *,
    max_p95_ms: float,
    min_throughput: float,
    max_peak_mb: float,
) -> list[str]:
    problems: list[str] = []
    if single["p95_ms"] > max_p95_ms:
        problems.append(f"p95 {single['p95_ms']}ms exceeds {max_p95_ms}ms")
    if single["throughput_rps"] < min_throughput:
        problems.append(f"throughput {single['throughput_rps']}/s below {min_throughput}/s")
    if single["peak_kb"] / 1024 > max_peak_mb:
        problems.append(f"peak heap {single['peak_kb'] / 1024:.1f}MB exceeds {max_peak_mb}MB")
    if corpus["p95_ms"] > max_p95_ms * 4:
        problems.append(f"corpus p95 {corpus['p95_ms']}ms exceeds {max_p95_ms * 4}ms")
    return problems


def build_report(single: dict[str, float], corpus: dict[str, Any], steps: dict[str, float]) -> str:
    steps_total = sum(steps.values()) or 1.0
    step_rows = [
        f"| `{name}` | {ms / max(1, int(single['iterations'])):.3f} | {ms / steps_total * 100:.1f}% |"
        for name, ms in sorted(steps.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return "\n".join(
        [
            "# 性能基准",
            "",
            "> 由 `python -m evals.benchmark --write-report` 生成，请勿手工编辑。",
            "",
            "## 结论",
            "",
            f"- 单 brief 重复 {int(single['iterations'])} 次（已预热 {WARMUP} 次）："
            f"**p50 {single['p50_ms']}ms / p95 {single['p95_ms']}ms / p99 {single['p99_ms']}ms**",
            f"- 吞吐 **{single['throughput_rps']} 次/秒**，Python 堆峰值 **{single['peak_kb'] / 1024:.2f}MB**",
            f"- 全评测集 {corpus['cases']} 个真实 brief 扫一遍："
            f"p95 {corpus['p95_ms']}ms，耗时 {corpus['wall_s']}s",
            "",
            "## 分步耗时（平均每次）",
            "",
            "| 步骤 | 毫秒 | 占比 |",
            "|---|---|---|",
            *step_rows,
            "",
            "## 运行环境",
            "",
            f"- Python {platform.python_version()} · {platform.system()} {platform.release()}",
            f"- 机器：{platform.machine()} / {platform.processor() or 'unknown'}",
            "",
            "## 怎么读这些数字",
            "",
            "这是**离线规则 provider** 的开销，不是模型延迟。它测的是检索、解析、校验、",
            "修复循环与 trace 的常数成本——真实模型的端到端延迟由 provider 决定，会在",
            "`docs/cost-latency-simulation.md` 的价格卡里单独估算。",
            "",
            "门禁设在 `p95 < 50ms`、`吞吐 > 50 次/秒`、`堆峰值 < 64MB`，比实测宽一个数量级：",
            "目的是抓「意外的 O(n²)」「每次调用重载知识库」这类回归，而不是卡 CI 抖动。",
            "",
            "展示页读取的是仓库里提交的快照 `evals/benchmark.json`（本机测得），而不是 CI 的",
            "实时结果——时间数字在不同机器上无法复现，放进一致性门禁只会造成假失败。",
            "刷新快照：`python -m evals.benchmark --out evals/benchmark.json --write-report`。",
            "",
            "## 已知限制",
            "",
            "- 内存用 `tracemalloc` 只统计 Python 堆，不含解释器与 C 扩展的常驻内存",
            "- 单机测量，未做并发压测；API 是 async 但流水线是同步计算",
            "- 数字随机器变化，跨机器比较无意义，只与同机历史版本比较",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan offline benchmark")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "benchmark.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--max-p95-ms", type=float, default=50.0)
    parser.add_argument("--min-throughput", type=float, default=50.0)
    parser.add_argument("--max-peak-mb", type=float, default=64.0)
    args = parser.parse_args(argv)

    iterations = max(10, args.iterations)
    sample = measure(iterations)
    single = sample.metrics()
    corpus = sweep()
    payload = {
        "single": single,
        "corpus": corpus,
        "steps_ms_total": {k: round(v, 2) for k, v in sample.step_ms.items()},
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(build_report(single, corpus, sample.step_ms), encoding="utf-8")

    print(
        f"iterations: {int(single['iterations'])} | p50 {single['p50_ms']}ms | "
        f"p95 {single['p95_ms']}ms | p99 {single['p99_ms']}ms | "
        f"{single['throughput_rps']}/s | peak {single['peak_kb'] / 1024:.2f}MB"
    )
    print(f"corpus: {corpus['cases']} cases | p95 {corpus['p95_ms']}ms | {corpus['status_counts']}")
    problems = (
        check(
            single,
            corpus,
            max_p95_ms=args.max_p95_ms,
            min_throughput=args.min_throughput,
            max_peak_mb=args.max_peak_mb,
        )
        if args.check
        else []
    )
    if problems:
        print("BENCHMARK GATE FAILED:")
        for problem in problems:
            print(f"  - {problem}")
    elif args.check:
        print("benchmark gate: OK")
    if args.write_report:
        print(f"report: {args.report_path}")
    print(f"raw: {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
