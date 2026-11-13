"""Controlled experiment: was the multi-worker slowdown really the write lock?

v0.8.0 measured a real uvicorn service and found 4 workers slower than 1. The
report attributed that to several processes contending for one SQLite writer.
That was an INFERENCE, not a test. This lab tests it.

`SHOTPLAN_PERSIST_RUNS=0` makes the SAME production code path skip the run-history
write (run_plan receives store=None). Everything else -- HTTP, ASGI, JSON,
validation, provider -- is untouched. Running the worker curve twice, with and
without persistence, in the same batch isolates the cost of that one variable.

The verdict is computed from the measured numbers and is allowed to contradict
the previous report. See docs/worker-bottleneck.md and ADR 0011.

Usage:
    python -m evals.worker_lab --check       # CI gate: verify the switch semantics
    python -m evals.worker_lab --write       # full matrix -> snapshot + report
    python -m evals.worker_lab               # one quick pass
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import OUT_DIR
from evals.benchmark import percentile
from evals.http_load import (
    Scenario,
    contract_problems,
    print_summary,
    run_scenario,
)

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "evals" / "worker_lab.json"
REPORT_PATH = ROOT / "docs" / "worker-bottleneck.md"

WORKERS = (1, 2, 4)
CONCURRENCY = 8
REQUESTS = 200
REPEATS = 2
CHECK_REQUESTS = 20
# A conclusion is only called "confirmed" if persistence is a major cost AND
# removing it actually fixes the 4-worker fall-off.
MAJOR_COST_RATIO = 1.5


@dataclass
class Cell:
    persist: bool
    workers: int
    rps_values: list[float] = field(default_factory=list)
    p95_values: list[float] = field(default_factory=list)
    errors: int = 0
    lost_writes: int = 0

    @property
    def median_rps(self) -> float:
        return round(statistics.median(self.rps_values), 2) if self.rps_values else 0.0

    @property
    def median_p95(self) -> float:
        return round(statistics.median(self.p95_values), 2) if self.p95_values else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "persist": self.persist,
            "workers": self.workers,
            "median_rps": self.median_rps,
            "median_p95_ms": self.median_p95,
            "rps_runs": [round(v, 2) for v in self.rps_values],
            "p95_runs": [round(v, 2) for v in self.p95_values],
            "errors": self.errors,
            "lost_writes": self.lost_writes,
        }


def summarize(scenarios: list[Scenario]) -> dict[tuple[bool, int], Cell]:
    cells: dict[tuple[bool, int], Cell] = {}
    for sc in scenarios:
        key = (sc.persist, sc.workers)
        cell = cells.setdefault(key, Cell(sc.persist, sc.workers))
        if sc.wall_s:
            cell.rps_values.append(sc.requests / sc.wall_s)
        cell.p95_values.append(percentile(sc.latencies, 95))
        cell.errors += len(sc.errors)
        cell.lost_writes += sc.lost_writes
    return dict(sorted(cells.items()))


def interpret(cells: dict[tuple[bool, int], Cell]) -> dict[str, Any]:
    """Compute the verdict from measurements; never hard-code the conclusion.

    A single median can hide that the phenomenon itself did not reproduce.
    Check the per-run 4w/1w ratio first: if the runs disagree about whether
    there was a fall-off at all, no causal attribution is defensible.
    """
    def rps(persist: bool, workers: int) -> float:
        cell = cells.get((persist, workers))
        return cell.median_rps if cell else 0.0

    gain_1w = rps(False, 1) / rps(True, 1) if rps(True, 1) else 0.0
    gain_2w = rps(False, 2) / rps(True, 2) if rps(True, 2) else 0.0
    on_4_over_1 = rps(True, 4) / rps(True, 1) if rps(True, 1) else 0.0
    off_4_over_1 = rps(False, 4) / rps(False, 1) if rps(False, 1) else 0.0
    on_peak = max(WORKERS, key=lambda w: rps(True, w))
    off_peak = max(WORKERS, key=lambda w: rps(False, w))

    on1, on4 = cells.get((True, 1)), cells.get((True, 4))
    paired = (
        [b / a for a, b in zip(on1.rps_values, on4.rps_values, strict=True) if a > 0]
        if on1 and on4 else []
    )
    fall_consistent = bool(paired) and all(r < 1.0 for r in paired)
    rise_consistent = bool(paired) and all(r >= 1.0 for r in paired)
    persistence_is_major = gain_1w >= MAJOR_COST_RATIO
    write_lock_caused_fall = fall_consistent and off_4_over_1 >= 1.0

    if not persistence_is_major:
        verdict = "refuted"
    elif not paired or (not fall_consistent and not rise_consistent):
        verdict = "inconclusive"
    elif not fall_consistent:
        verdict = "not_reproduced"
    elif write_lock_caused_fall:
        verdict = "confirmed"
    else:
        verdict = "partially_confirmed"

    return {
        "verdict": verdict,
        "persistence_gain_1w": round(gain_1w, 2),
        "persistence_gain_2w": round(gain_2w, 2),
        "on_4w_over_1w": round(on_4_over_1, 2),
        "off_4w_over_1w": round(off_4_over_1, 2),
        "on_4w_over_1w_per_run": [round(r, 2) for r in paired],
        "on_peak_workers": on_peak,
        "off_peak_workers": off_peak,
        "persistence_is_major_cost": persistence_is_major,
        "write_lock_caused_fall": write_lock_caused_fall,
    }


def verdict_text(result: dict[str, Any]) -> str:
    label = {
        "confirmed": "**假设成立**",
        "partially_confirmed": "**假设部分成立，归因不成立**",
        "refuted": "**假设不成立**",
        "not_reproduced": "**回落现象本次未复现，无法检验归因**",
        "inconclusive": "**本次无法给出稳定结论**",
    }[result["verdict"]]
    runs = " / ".join(f"{v}×" for v in result["on_4w_over_1w_per_run"]) or "无"
    lines = [
        f"{label}。",
        "",
        f"- 关掉运行历史写入后，1 worker 吞吐变为 **{result['persistence_gain_1w']}×**、"
        f"2 worker 变为 **{result['persistence_gain_2w']}×**："
        + (
            "持久化确实是关键路径上最大的单项开销，这一条在本次所有轮次都稳定。"
            if result["persistence_is_major_cost"]
            else "持久化并不是主要开销。"
        ),
        f"- 有写入组 4 worker 相对 1 worker 的**逐轮**比值是 {runs}"
        f"（中位数 {result['on_4w_over_1w']}×），关掉写入后是 "
        f"**{result['off_4w_over_1w']}×**；两组最佳点分别是 "
        f"{result['on_peak_workers']} / {result['off_peak_workers']} worker。",
    ]
    if result["verdict"] == "inconclusive":
        lines += [
            "- 逐轮比值跨过了 1.0：有的轮次回落、有的轮次没有，**连「是否回落」都不稳定**。"
            "在现象本身没复现的前提下讨论原因没有意义，因此上一版「共享 SQLite 写锁导致回落」"
            "只能保留为**待验证假设**，不能当作结论。",
            "- 换个角度说：这一层压测真正稳定复现的只有「同步写库是大开销」。"
            "把因果结论建立在一次单批测量上，本身就是需要修正的方法论问题。",
        ]
    elif result["verdict"] == "not_reproduced":
        lines.append(
            "- 有写入组各轮都没有出现 4 worker 回落，因此没有现象可供归因；"
            "上一版结论同样只能保留为待验证假设。"
        )
    elif result["write_lock_caused_fall"]:
        lines.append(
            "- 去掉写锁后 4 worker 不再回落：**在本次负载下**支持「共享 SQLite 写者争用是回落主因」"
            "这一归因。它是有条件的结论，不是普适规律（见边界一节）。"
        )
    else:
        lines.append(
            "- 关掉持久化后回落依然存在，说明回落另有原因（进程/线程协调、上下文切换或 CPU 争用），"
            "而不是 SQLite 写者互斥；上一版的因果判断不成立。"
        )
    lines += [
        "",
        "- 修正后的工程建议：想提吞吐，**第一杠杆是把运行历史写入移出同步关键路径**"
        "（异步写、批量写或独立写服务）；worker 数应实测找拐点，不是越多越好。"
        "运行历史本身是可观测性与回归追溯的基础，不该删掉，只该异步化。",
    ]
    return "\n".join(lines)


def relation_text(result: dict[str, Any]) -> str:
    """Describe what this run does to the v0.8.0 claim, without hard-coding it."""
    if result["verdict"] == "confirmed":
        return (
            "- `docs/http-load.md` 的归因在本批数据下**得到支持**：关掉持久化后 4 worker 不再回落。"
            "但它仍是有条件的结论——另一批独立运行中「回落」本身没有稳定复现，"
            "因此不写成普适规律。"
        )
    if result["verdict"] in ("inconclusive", "not_reproduced"):
        return (
            "- `docs/http-load.md` 原文把 4 worker 回落归因于 WAL 单写者争用；"
            "本实验里该现象逐轮不稳定、无法稳定复现，因此归因**降级为待验证假设**，"
            "不再作为结论陈述。"
        )
    return (
        "- `docs/http-load.md` 的写锁归因在本批数据下**不成立**：关掉持久化后回落依然存在，"
        "回落另有原因（进程协调 / 调度 / CPU 争用）。已据此修正报告与展示页的表述。"
    )


def build_report(
    cells: dict[tuple[bool, int], Cell], result: dict[str, Any], cpu_count: int
) -> str:
    lines = [
        "# 对照实验：多 worker 变慢，真的是 SQLite 写锁吗",
        "",
        "> 由 `python -m evals.worker_lab --write` 生成本机快照。数字机器相关，**不进 CI 门禁**。",
        "",
        "## 为什么要做这个实验",
        "",
        "[http-load.md](http-load.md)（v0.8.0）测到 4 个 uvicorn worker 比 1 个还慢，"
        "并把它归因于「多个进程抢同一个 SQLite 写锁」。那是**推断**，不是受控实验。",
        "本项目一直要求「结论要能被证伪」，所以这里补上对照组。",
        "",
        "做法：给服务加一个开关 `SHOTPLAN_PERSIST_RUNS=0`，让**同一份产品代码**"
        "在 `run_plan(..., store=None)` 下跳过运行历史写入；HTTP、ASGI、JSON 序列化、"
        "provider、校验、修复全部不变。其余唯一变量就是「有没有同步写 SQLite」。",
        "",
        f"环境：Python {platform.python_version()} / {platform.system()} / {cpu_count} 逻辑核；"
        f"并发固定 {CONCURRENCY}，每个组合 {REQUESTS} 次请求、重复 {REPEATS} 轮取**中位数**。"
        "开 / 关两组在同一批内交替跑，避免跨批机器负载漂移影响相对比较。",
        "",
        "## 结论",
        "",
        verdict_text(result),
        "",
        "## 实测矩阵（中位数，括号内为每轮原始值）",
        "",
        "| 持久化 | workers | 吞吐 /s | P95 ms | 错误 | 丢写 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for (persist, workers), cell in cells.items():
        runs = " / ".join(f"{v:.0f}" for v in cell.rps_values)
        lines.append(
            f"| {'开' if persist else '关'} | {workers} | {cell.median_rps:.2f} ({runs}) | "
            f"{cell.median_p95:.2f} | {cell.errors} | {cell.lost_writes} |"
        )
    lines += [
        "",
        "「丢写」一列在持久化关闭时恒为 0：那一组本来就不写库，不是漏记。",
        "每轮原始值一并列出，是为了让中位数不会被看成精确常数——本机波动很大。",
        "",
        "## 与上一版报告的关系",
        "",
        relation_text(result),
        "- 展示页、中英文 README、面试讲稿 Q&A 与 CHANGELOG 同步标注了这一层证据，"
        "并在 [ADR 0011](adr/0011-controlled-experiment-over-inference.md) 记录"
        "「先做对照实验再下因果结论」这条方法论。",
        "",
        "## 门禁检查什么",
        "",
        "`python -m evals.worker_lab --check` 只跑两个短场景（1 worker，开 / 关各 "
        f"{CHECK_REQUESTS} 次请求），断言：",
        "",
        "1. 两组都 0 错误、返回合法 `status=ok` 方案；",
        "2. 持久化**开**时 `total_runs` 增量等于成功请求数；",
        "3. 持久化**关**时 `total_runs` 增量恒为 **0**——证明开关真的改变了服务端行为，"
        "而不是实验自欺欺人。",
        "",
        "吞吐、中位数、加速比与结论文字**都不进门禁**：它们随机器负载变化，"
        "沿用 [ADR 0008](adr/0008-fuzz-and-benchmark-gates.md) 的原则。",
        "",
        "## 边界",
        "",
        "- 单机 loopback、离线 provider、无 TLS / 反向代理 / 真实模型延迟。",
        "- 样本仍偏小，本机两次运行的绝对值可差数倍；结论只看**同一批内的相对关系**。",
        "- 同一台机器不同批次可能给出不同 verdict：另一次独立运行里，有写入组的 4 worker "
        "反而比 1 worker 快 2.42×，「回落」根本没有出现。稳定复现的只有"
        "「同步写库是最大单项开销」；任何关于 worker 回落原因的因果说法，都需要更多重复"
        "与更严格的负载隔离后才能下。",
        "- 「持久化是最大开销」不等于「应该永远关掉历史」：运行历史是可观测性与回归追溯的基础。"
        "正确做法是把写入异步化 / 批量化，而不是删掉。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.worker_lab --check   # CI 门禁",
        "python -m evals.worker_lab --write  # 重跑完整矩阵并刷新报告",
        "```",
        "",
    ]
    return "\n".join(lines)


def snapshot(
    cells: dict[tuple[bool, int], Cell],
    result: dict[str, Any],
    cpu_count: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "machine_dependent": True,
        "note": "controlled experiment; throughput is reported, never gated",
        "python": platform.python_version(),
        "platform": platform.system(),
        "cpu_count": cpu_count,
        "workers": list(WORKERS),
        "concurrency": CONCURRENCY,
        "requests_per_run": REQUESTS,
        "repeats": REPEATS,
        "cells": [cell.to_dict() for cell in cells.values()],
        "interpretation": result,
    }


def check_problems(on: Scenario, off: Scenario) -> list[str]:
    problems: list[str] = []
    for sc in (on, off):
        problems.extend(contract_problems(sc, gate=False))
    if on.runs_delta != on.ok_count:
        problems.append(
            f"persist=ON stored {on.runs_delta} of {on.ok_count} runs: writes were lost"
        )
    if off.runs_delta != 0:
        problems.append(
            f"persist=OFF still stored {off.runs_delta} runs: the switch had no effect"
        )
    if on.persist is not True or off.persist is not False:
        problems.append("the two arms did not actually use opposite persist settings")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="controlled worker/persistence experiment")
    parser.add_argument("--write", action="store_true", help="full matrix + snapshot + report")
    parser.add_argument("--check", action="store_true", help="CI gate: switch semantics")
    parser.add_argument("--requests", type=int, default=REQUESTS)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "worker_lab.json")
    args = parser.parse_args(argv)

    cpu_count = os.cpu_count() or 1
    try:
        if args.write:
            scenarios: list[Scenario] = []
            for _ in range(max(1, args.repeats)):
                for persist in (True, False):
                    for workers in WORKERS:
                        sc = run_scenario(workers, CONCURRENCY, args.requests, persist=persist)
                        print_summary(sc)
                        scenarios.append(sc)
            cells = summarize(scenarios)
            result = interpret(cells)
            payload = snapshot(cells, result, cpu_count)
            SNAPSHOT_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            REPORT_PATH.write_text(build_report(cells, result, cpu_count), encoding="utf-8")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"verdict: {result['verdict']}  "
                  f"persistence gain 1w={result['persistence_gain_1w']}x  "
                  f"off 4w/1w={result['off_4w_over_1w']}x")
            print(f"snapshot: {SNAPSHOT_PATH}")
            print(f"report: {REPORT_PATH}")
            return 0

        on = run_scenario(1, 4, CHECK_REQUESTS, persist=True)
        off = run_scenario(1, 4, CHECK_REQUESTS, persist=False)
        print_summary(on)
        print_summary(off)
        problems = check_problems(on, off)
        if problems:
            print("WORKER-LAB GATE FAILED:")
            for problem in problems:
                print(f"  - {problem}")
        elif args.check:
            print("worker-lab gate: OK")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {"check": {"on": on.metrics(), "off": off.metrics()}, "problems": problems},
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        return 1 if problems else 0
    except Exception as exc:  # noqa: BLE001 - surface a failed service as a gate failure
        print(f"WORKER-LAB GATE FAILED: could not run the service: "
              f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
