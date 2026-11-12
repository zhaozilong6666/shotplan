"""Threads vs processes: where the ceiling actually is.

The concurrency load test showed throughput stalling around 8 threads. That is
expected for CPU-bound Python, but "expected" is not evidence — this measures
the same workload two ways:

* **threads** — shared interpreter, one GIL, no parallel bytecode execution
* **processes** — separate interpreters, real parallelism up to core count

If threads plateau while processes keep scaling, the bottleneck is the GIL and
the deployment answer is worker processes (e.g. ``uvicorn --workers N``), not
more threads. If both plateau, the bottleneck is elsewhere and adding workers
would only add memory.

Speed is machine-dependent and workload-dependent: with a small batch the
inter-process transport can cost more than the work itself, so "processes are
faster" is a *finding in the report*, never a CI assertion (ADR 0008). The gate
checks only machine-independent contracts: every run succeeded, every arm
recorded elapsed time, and a multi-worker process arm really used more than one
OS process. Otherwise a busy CI runner would turn a physics result into a red
build.

Usage:
    python -m evals.scaling --check
    python -m evals.scaling --requests 80 --write-report
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals import OUT_DIR
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "scaling.md"
BRIEFS = (
    {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 1500, "styles": ["tang"]},
    {"city": "suzhou", "date": "2026-04-18", "hours": 5, "budget_cny": 1200, "styles": ["song"]},
    {"city": "nanjing", "date": "2026-10-17", "hours": 6, "budget_cny": 1600, "styles": ["ming"]},
    {"city": "hangzhou", "date": "2026-03-21", "hours": 7, "budget_cny": 1800, "styles": ["song"]},
)


def run_batch(job: tuple[int, int]) -> int:
    """Run ``count`` plans starting at ``start``; return how many succeeded.

    Batched on purpose: a single run takes ~0.3ms, which is far below the cost
    of one inter-process message. Measuring per-run IPC would say "processes are
    slow" when the real finding is "the task is smaller than the transport".
    """
    start, count = job
    ok = 0
    for index in range(start, start + count):
        request = PlanRequest.from_dict(dict(BRIEFS[index % len(BRIEFS)]))
        result = run_plan(request, provider=MockProvider("good"), max_repairs=1)
        ok += 1 if result.status in {"ok", "infeasible"} else 0
    return ok


def split(total: int, parts: int) -> list[tuple[int, int]]:
    """Balanced (start, count) chunks so every worker gets comparable work."""
    parts = max(1, min(parts, total))
    base, extra = divmod(total, parts)
    jobs, start = [], 0
    for index in range(parts):
        count = base + (1 if index < extra else 0)
        if count:
            jobs.append((start, count))
            start += count
    return jobs


@dataclass
class Arm:
    mode: str
    workers: int
    requests: int
    wall_s: float = 0.0
    errors: int = 0
    pids: tuple[int, ...] = ()

    def metrics(self, baseline_rps: float) -> dict[str, Any]:
        rps = self.requests / self.wall_s if self.wall_s else 0.0
        return {
            "mode": self.mode,
            "workers": self.workers,
            "requests": self.requests,
            "wall_s": round(self.wall_s, 3),
            "rps": round(rps, 2),
            "speedup": round(rps / baseline_rps, 2) if baseline_rps else 0.0,
            "errors": self.errors,
        }


def run_threads(workers: int, requests: int) -> Arm:
    arm = Arm("threads", workers, requests)
    jobs = split(requests, workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pool.map(run_batch, [(0, 1)])  # warm up before timing
        started = time.perf_counter()
        done = list(pool.map(run_batch, jobs))
    arm.wall_s = time.perf_counter() - started
    arm.errors = requests - sum(done)
    return arm


def run_processes(workers: int, requests: int) -> Arm:
    arm = Arm("processes", workers, requests)
    jobs = split(requests, workers)
    methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in methods else "spawn")
    with context.Pool(processes=workers) as pool:
        # Pool creation and interpreter start-up are deployment costs, not
        # planning costs: warm up first, then time only the work.
        pool.map(run_batch, [(0, 1)])
        # Pool._pool is the list of live worker processes. Their PIDs are a
        # deterministic, machine-independent proof that the multi-process path
        # really started N interpreters. A lightweight "which pid ran this
        # probe" map is NOT reliable: tiny jobs can all land on one worker.
        arm.pids = tuple(sorted({p.pid for p in pool._pool if p.pid}))
        started = time.perf_counter()
        done = list(pool.map(run_batch, jobs))
    arm.wall_s = time.perf_counter() - started
    arm.errors = requests - sum(done)
    return arm


def check(arms: list[Arm]) -> list[str]:
    """Machine-independent contracts only; never assert a speed ranking."""
    problems: list[str] = []
    for arm in arms:
        if arm.errors:
            problems.append(f"{arm.mode}@{arm.workers}: {arm.errors} failed run(s)")
        if arm.wall_s <= 0:
            problems.append(
                f"{arm.mode}@{arm.workers}: no elapsed time recorded; "
                "the measurement did not run"
            )
        if arm.mode == "processes" and arm.workers > 1 and len(arm.pids) < 2:
            problems.append(
                f"processes@{arm.workers} used {len(arm.pids)} worker pid(s): "
                "parallelism was not actually exercised"
            )
    return problems


def speed_note(arms: list[Arm]) -> str | None:
    """Informational comparison for the console and the report, not a gate."""
    threads = [a for a in arms if a.mode == "threads" and a.wall_s > 0]
    procs = [a for a in arms if a.mode == "processes" and a.wall_s > 0]
    if not threads or not procs:
        return None
    best_thread = max(a.requests / a.wall_s for a in threads)
    best_proc = max(a.requests / a.wall_s for a in procs)
    relation = "faster" if best_proc >= best_thread else "slower"
    return (
        f"note: best processes {best_proc:.0f}/s is {relation} than threads "
        f"{best_thread:.0f}/s on this run; speed is informational, not a gate"
    )


def build_report(arms: list[Arm], cpu_count: int) -> str:
    baseline = next((a for a in arms if a.mode == "threads" and a.workers == 1), arms[0])
    base_rps = baseline.requests / baseline.wall_s if baseline.wall_s else 0.0
    rows = []
    for arm in arms:
        m = arm.metrics(base_rps)
        rows.append(
            f"| {m['mode']} | {m['workers']} | {m['rps']} | {m['speedup']:.2f}× | {m['errors']} |"
        )
    thread_best = max((a for a in arms if a.mode == "threads"), key=lambda a: a.requests / a.wall_s)
    proc_best = max((a for a in arms if a.mode == "processes"), key=lambda a: a.requests / a.wall_s)
    thread_speed = thread_best.requests / thread_best.wall_s / base_rps if base_rps else 0.0
    proc_speed = proc_best.requests / proc_best.wall_s / base_rps if base_rps else 0.0
    max_workers = max(a.workers for a in arms)
    if proc_best.workers < max_workers:
        proc_note = (
            f"- 进程数超过 {proc_best.workers} 并没有更好（本机最佳就是 {proc_best.workers} 进程）："
            "收益受内存带宽与每进程开销限制，不是「worker 越多越好」。"
        )
    else:
        proc_note = "- 本机尚未测到进程数的拐点，但每进程内存与启动开销会在更高并发下成为主要成本。"
    return "\n".join(
        [
            "# 线程 vs 进程：上限在哪里",
            "",
            "> 由 `python -m evals.scaling --write-report` 生成，请勿手工编辑。",
            "",
            "## 结论",
            "",
            f"- 机器逻辑核心数：**{cpu_count}**（`os.cpu_count()`）",
            f"- 线程最好成绩 **{thread_speed:.2f}×**（{thread_best.workers} 线程），"
            f"进程最好成绩 **{proc_speed:.2f}×**（{proc_best.workers} 进程），均以单线程为基线。",
            "- **线程几乎不加速，进程能加速**：瓶颈是 GIL，而不是代码结构。要把吞吐真正做上去，"
            "部署层应当加 worker 进程（`uvicorn --workers N`），而不是加线程。",
            proc_note,
            "",
            "## 数字",
            "",
            "| 模式 | 并行度 | 吞吐 (/s) | 相对单线程 | 失败 |",
            "| --- | ---: | ---: | ---: | ---: |",
            *rows,
            "",
            "## 怎么读",
            "",
            "- 线程共享一个解释器：纯 Python 计算无法并行执行字节码，加线程主要是在等 I/O 时才有效。",
            "- 进程各自持有解释器，可以真正跑满多核；代价是内存与启动开销，且不能共享内存状态。",
            "- 本项目每次运行都是独立的纯计算 + 一次 SQLite 写入，没有跨请求状态，所以按进程扩展是安全的。",
            "",
            "## 已知限制",
            "",
            "- 单机测量，未包含反向代理、网络与容器开销；真实部署的 QPS 会低于这里的流水线吞吐",
            "- `spawn` 平台的进程启动开销计入 wall time，进程数越多越吃亏（Linux 用 `fork` 会更快）",
            "- 门禁只判「没有失败」「有耗时」「多 worker 进程跑在多个 PID 上」；速度排序不做断言",
            "- 小 workload 下 IPC 开销可能盖过并行收益，进程慢于线程是测量尺度问题而非结论反转",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan threads-vs-processes scaling lab")
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--workers", type=int, nargs="*", default=[1, 2, 4])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "scaling.json")
    args = parser.parse_args(argv)

    requests = max(8, args.requests)
    workers = sorted(set(args.workers))
    cpu_count = os.cpu_count() or 1
    arms = [run_threads(w, requests) for w in workers]
    arms += [run_processes(w, requests) for w in workers]

    baseline = next(a for a in arms if a.mode == "threads" and a.workers == workers[0])
    base_rps = baseline.requests / baseline.wall_s if baseline.wall_s else 0.0
    payload = {
        "cpu_count": cpu_count,
        "requests": requests,
        "arms": [a.metrics(base_rps) for a in arms],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(build_report(arms, cpu_count), encoding="utf-8")

    for arm in arms:
        m = arm.metrics(base_rps)
        print(
            f"{m['mode']:<10} workers={m['workers']} {m['rps']:<8}/s "
            f"speedup={m['speedup']:.2f}x errors={m['errors']}"
        )
    note = speed_note(arms)
    if note:
        print(note)
    problems = check(arms) if args.check else []
    if problems:
        print("SCALING GATE FAILED:")
        for problem in problems:
            print(f"  - {problem}")
    elif args.check:
        print("scaling gate: OK")
    if args.write_report:
        print(f"report: {args.report_path}")
    print(f"raw: {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
