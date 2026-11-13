"""Does write-behind earn its complexity?

v0.9.0 established, with a controlled on/off experiment, that the synchronous
run-history write is the largest single cost on the request path. The report
ended with a recommendation: move the write off the critical path, do not delete
run history. This lab checks whether the recommendation actually works.

Three arms over **the same product code**:

* ``off``   - no write at all. The lower bound, and not shippable: no history.
* ``sync``  - committed before the response. Durable, slow, the status quo.
* ``async`` - handed to the per-process writer thread, batched, flushed on
  shutdown. Same rows, later.

The useful number is not "async is faster than sync" in the abstract. It is
**what fraction of the write cost async gives back**, and **whether any row is
lost when the server is asked to stop**. Both are computed from the measurements
and both are allowed to come out badly. See docs/persistence.md and ADR 0012.

Usage:
    python -m evals.persist_lab --check   # CI gate: three arms, three contracts
    python -m evals.persist_lab --write   # full matrix -> snapshot + report
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
from evals.http_load import Scenario, contract_problems, print_summary, run_scenario

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "evals" / "persist_lab.json"
REPORT_PATH = ROOT / "docs" / "persistence.md"

ARMS = ("off", "sync", "async")
WORKERS = (1, 4)
CONCURRENCY = 8
REQUESTS = 150
REPEATS = 2
CHECK_REQUESTS = 30


@dataclass
class Cell:
    mode: str
    workers: int
    rps_values: list[float] = field(default_factory=list)
    p95_values: list[float] = field(default_factory=list)
    drain_values: list[float] = field(default_factory=list)
    errors: int = 0
    lost_writes: int = 0

    @property
    def median_rps(self) -> float:
        return round(statistics.median(self.rps_values), 2) if self.rps_values else 0.0

    @property
    def median_p95(self) -> float:
        return round(statistics.median(self.p95_values), 2) if self.p95_values else 0.0

    @property
    def median_drain(self) -> float:
        return round(statistics.median(self.drain_values), 3) if self.drain_values else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "workers": self.workers,
            "median_rps": self.median_rps,
            "median_p95_ms": self.median_p95,
            "median_drain_s": self.median_drain,
            "rps_runs": [round(v, 2) for v in self.rps_values],
            "p95_runs": [round(v, 2) for v in self.p95_values],
            "errors": self.errors,
            "lost_writes": self.lost_writes,
        }


def summarize(scenarios: list[Scenario]) -> dict[tuple[str, int], Cell]:
    cells: dict[tuple[str, int], Cell] = {}
    for sc in scenarios:
        cell = cells.setdefault((sc.mode, sc.workers), Cell(sc.mode, sc.workers))
        if sc.wall_s:
            cell.rps_values.append(sc.requests / sc.wall_s)
        cell.p95_values.append(percentile(sc.latencies, 95))
        cell.drain_values.append(sc.drain_s)
        cell.errors += len(sc.errors)
        cell.lost_writes += sc.lost_writes
    return dict(sorted(cells.items()))


def interpret(cells: dict[tuple[str, int], Cell]) -> dict[str, Any]:
    """Everything downstream of this is computed, not asserted."""

    def rps(mode: str, workers: int) -> float:
        cell = cells.get((mode, workers))
        return cell.median_rps if cell else 0.0

    def p95(mode: str, workers: int) -> float:
        cell = cells.get((mode, workers))
        return cell.median_p95 if cell else 0.0

    write_cost_1w = rps("off", 1) / rps("sync", 1) if rps("sync", 1) else 0.0
    write_cost_4w = rps("off", 4) / rps("sync", 4) if rps("sync", 4) else 0.0
    recovery_1w = rps("async", 1) / rps("sync", 1) if rps("sync", 1) else 0.0
    recovery_4w = rps("async", 4) / rps("sync", 4) if rps("sync", 4) else 0.0
    headroom_1w = rps("off", 1) - rps("sync", 1)
    recovered_1w = (
        (rps("async", 1) - rps("sync", 1)) / headroom_1w if headroom_1w > 0 else 0.0
    )
    lost = sum(cell.lost_writes for key, cell in cells.items() if key[0] != "off")

    if write_cost_1w < 1.2:
        verdict = "no_bottleneck"
    elif lost:
        verdict = "unsafe"
    elif recovery_1w >= 1.3:
        verdict = "confirmed"
    elif recovery_1w > 1.05:
        verdict = "partial"
    else:
        verdict = "no_gain"

    return {
        "verdict": verdict,
        "write_cost_1w": round(write_cost_1w, 2),
        "write_cost_4w": round(write_cost_4w, 2),
        "async_recovery_1w": round(recovery_1w, 2),
        "async_recovery_4w": round(recovery_4w, 2),
        "share_of_cost_recovered_1w": round(recovered_1w, 3),
        "p95_sync_1w": round(p95("sync", 1), 2),
        "p95_async_1w": round(p95("async", 1), 2),
        "lost_writes": lost,
    }


def verdict_text(result: dict[str, Any]) -> str:
    label = {
        "confirmed": "**写入异步化确实把成本还回来了**",
        "partial": "**写入异步化有效，但只还回了一部分**",
        "no_gain": "**异步化没有带来可测量的收益**",
        "unsafe": "**异步化丢了数据，收益不可用**",
        "no_bottleneck": "**本次测量里同步写库根本不是瓶颈**",
    }[result["verdict"]]
    pct = int(round(result["share_of_cost_recovered_1w"] * 100))
    lines = [f"{label}。", ""]
    if result["verdict"] == "no_bottleneck":
        lines.append(
            f"- 关掉写库只比同步写快 **{result['write_cost_1w']}×**，低于 1.2× 的判定线，"
            "因此这一批数据不支持「写库是主要开销」。"
        )
    else:
        lines += [
            f"- 同步写库的代价：关掉写入后 1 worker 快 **{result['write_cost_1w']}×**、"
            f"4 worker 快 **{result['write_cost_4w']}×**。这是上限，不是可交付状态（没有历史）。",
            f"- 异步写库的收益：相对同步写入，1 worker **{result['async_recovery_1w']}×**、"
            f"4 worker **{result['async_recovery_4w']}×**，"
            f"即拿回了写库开销的 **{pct}%**。",
            f"- P95：同步 {result['p95_sync_1w']}ms → 异步 {result['p95_async_1w']}ms"
            "（1 worker，中位数）。",
        ]
    if result["lost_writes"]:
        lines.append(
            f"- **但丢了 {result['lost_writes']} 条记录**：异步组在排空窗口内没有全部落库，"
            "收益不可用，需要先修正确性。"
        )
    else:
        lines.append(
            "- **没有丢写**：三组都满足「每个被接受的请求最终都落库」，异步组是在排空窗口内等齐的。"
        )
    return "\n".join(lines)


def honest_limits(result: dict[str, Any] | None = None) -> list[str]:
    return [
        "- 异步只是把**等待**移出请求路径，并没有减少写库的总工作量：同样的行、同样的 fsync，"
        "只是改成每 32 行一次提交，并由后台线程承担。",
        "- 每个 worker 进程各有一个写线程和一个队列，跨进程仍然争用同一个 SQLite 写者；"
        "worker 越多，这条争用越明显。真正的多机方案需要独立写服务或消息队列。",
        "- 离线 mock provider 下服务本身很快，写库占比因此被放大；接入真实模型后，"
        "网络延迟会稀释这个收益，比例会下降。",
        # The number in this sentence must come from the batch that produced
        # the report, not from the last time the sentence was edited — a
        # hand-updated "this batch measured X" went stale once already.
        f"- **跨批次不可比**：同一台机器上，v0.9.0 那批关掉写库只快 2.48×，本批是 "
        f"{result['write_cost_1w'] if result else 'N'}×；另一批用未修改的旧脚本重跑得到 7×。"
        "差异来自磁盘 / 临时目录 / 后台负载，不是代码。"
        "所以本报告只做**同批内**的相对比较，绝对吞吐不进任何结论。",
        "- 异步模式的持久性边界是「进程被 SIGKILL / 断电」：队列里未提交的行会丢。"
        "测试里用 `os._exit` 真实杀进程量过这个窗口（tests/test_write_behind.py），"
        "不是靠推断。优雅退出（SIGTERM / lifespan shutdown）会先排空，不丢。",
    ]


def build_report(
    cells: dict[tuple[str, int], Cell], result: dict[str, Any], cpu_count: int
) -> str:
    lines = [
        "# 写入异步化：把运行历史移出请求关键路径",
        "",
        "> 由 `python -m evals.persist_lab --write` 生成本机快照。数字机器相关，**不进 CI 门禁**。",
        "",
        "## 上一版留下的问题",
        "",
        "[worker-bottleneck.md](worker-bottleneck.md)（v0.9.0）用开/关对照实验确认了"
        "「同步写运行历史是请求关键路径上最大的单项开销」，并给出建议："
        "**把写入异步化，而不是删掉历史**。建议当时没有实现，也没有验证。",
        "这一版实现了，并且用同一个压测框架量它到底还回多少。",
        "",
        "## 三个实验组",
        "",
        "同一份产品代码，只改 `SHOTPLAN_PERSIST_MODE`：",
        "",
        "| 组 | 行为 | 可交付？ |",
        "| --- | --- | --- |",
        "| `off` | `run_plan(store=None)`，完全不写 | 否，没有历史就没有可观测性与回归追溯 |",
        "| `sync` | 响应返回前提交（v0.9.0 的行为，当前默认） | 是 |",
        "| `async` | 交给进程内写线程，批量提交，退出时排空 | 是，代价是硬杀时的丢失窗口 |",
        "",
        f"环境：Python {platform.python_version()} / {platform.system()} / {cpu_count} 逻辑核；"
        f"并发 {CONCURRENCY}，每组 {REQUESTS} 次请求、重复 {REPEATS} 轮取中位数，"
        "三组在同一批内交替执行。",
        "",
        "「排空」一列是最后一个响应返回之后，行数补齐所花的时间——异步的代价在这里，不在吞吐里。",
        "",
        "## 结论",
        "",
        verdict_text(result),
        "",
        "## 实测矩阵（中位数，括号内为每轮原始值）",
        "",
        "| 模式 | workers | 吞吐 /s | P95 ms | 排空 s | 错误 | 丢写 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for (mode, workers), cell in cells.items():
        runs = " / ".join(f"{v:.0f}" for v in cell.rps_values)
        lines.append(
            f"| `{mode}` | {workers} | {cell.median_rps:.2f} ({runs}) | "
            f"{cell.median_p95:.2f} | {cell.median_drain:.3f} | "
            f"{cell.errors} | {cell.lost_writes} |"
        )
    lines += [
        "",
        "## 实现要点",
        "",
        "1. **一个进程一个写线程**（`RunWriter`），不是一请求一个。SQLite 只有一个写者，"
        "让 N 个请求线程去抢锁，不如让它们把行交给一个线程批量提交。",
        "2. **批量提交**：`save_many` 在一个事务里 `executemany`，32 行一次 fsync，"
        "而不是 32 次。",
        "3. **不丢**：队列满时不丢行，退化为同步写并计数（`sync_fallbacks`）；"
        "批次失败重试一次，仍失败则计入 `failed` 而不是杀掉线程。",
        "4. **可观测**：`/api/config` 在 async 模式下暴露 `submitted / written / pending / "
        "batches / max_batch / sync_fallbacks`，队列积压是可见的。",
        "5. **退出排空**：FastAPI `lifespan` 在 shutdown 时 `flush`，SIGTERM 不丢；"
        "`os._exit` / 断电会丢队列里的行——这个窗口被真实测量，不靠推断。",
        "",
        "## 门禁检查什么",
        "",
        f"`python -m evals.persist_lab --check` 跑三个短场景（1 worker，每组 {CHECK_REQUESTS} 次），"
        "断言的是**契约**而不是速度：",
        "",
        "1. 三组都 0 错误、都返回合法 `status=ok` 方案；",
        "2. `off` 组 `total_runs` 增量为 0——开关真的生效；",
        "3. `sync` 与 `async` 组增量都等于成功请求数——异步模式在排空窗口内不丢写；",
        "4. 服务端回报的模式与实验分组一致——防止「改了环境变量但没生效」的自欺欺人。",
        "",
        "吞吐、加速比、排空时间与结论文字都不进门禁（[ADR 0008](adr/0008-fuzz-and-benchmark-gates.md)）。",
        "",
        "## 边界",
        "",
        *honest_limits(result),
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.persist_lab --check   # CI 门禁",
        "python -m evals.persist_lab --report  # 只重排报告（读提交的快照，不重测）",
        "python -m evals.persist_lab --write   # 重跑矩阵并刷新快照与报告",
        "```",
        "",
    ]
    return "\n".join(lines)


def snapshot(
    cells: dict[tuple[str, int], Cell], result: dict[str, Any], cpu_count: int
) -> dict[str, Any]:
    return {
        "version": 1,
        "machine_dependent": True,
        "note": "throughput/drain are reported, never gated; loss and switch semantics are",
        "python": platform.python_version(),
        "platform": platform.system(),
        "cpu_count": cpu_count,
        "arms": list(ARMS),
        "workers": list(WORKERS),
        "concurrency": CONCURRENCY,
        "requests_per_run": REQUESTS,
        "repeats": REPEATS,
        "cells": [cell.to_dict() for cell in cells.values()],
        "interpretation": result,
    }


def check_problems(off: Scenario, sync: Scenario, async_: Scenario) -> list[str]:
    problems: list[str] = []
    for sc in (off, sync, async_):
        problems.extend(contract_problems(sc, gate=False))
    if off.runs_delta != 0:
        problems.append(
            f"mode=off still stored {off.runs_delta} runs: the switch had no effect"
        )
    for sc in (sync, async_):
        if sc.runs_delta != sc.ok_count:
            problems.append(
                f"mode={sc.mode} stored {sc.runs_delta} of {sc.ok_count} runs "
                f"within the drain window: writes were lost"
            )
    for sc, expected in ((off, "off"), (sync, "sync"), (async_, "async")):
        if sc.mode != expected:
            problems.append(
                f"the service reported mode={sc.mode!r} but the arm was {expected!r}"
            )
    return problems


def cells_from_snapshot(snapshot: dict[str, Any]) -> dict[tuple[str, int], Cell]:
    """Rebuild the cells from a committed snapshot, without re-running.

    Raw rps/p95 runs are stored per cell, so medians are reproduced exactly;
    per-run drain times are not stored, only the median, which is all the
    report prints. This is what makes the report refreshable independently of
    the (slow, machine-dependent) measurement.
    """
    cells: dict[tuple[str, int], Cell] = {}
    for raw in snapshot["cells"]:
        cell = Cell(
            raw["mode"], raw["workers"],
            rps_values=list(raw["rps_runs"]),
            p95_values=list(raw["p95_runs"]),
            drain_values=[raw["median_drain_s"]],
            errors=raw["errors"],
            lost_writes=raw["lost_writes"],
        )
        cells[(cell.mode, cell.workers)] = cell
    return cells


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write-behind persistence experiment")
    parser.add_argument("--write", action="store_true", help="full matrix + snapshot + report")
    parser.add_argument("--check", action="store_true", help="CI gate: three-arm contract")
    parser.add_argument(
        "--report", action="store_true",
        help="rebuild docs/persistence.md from the committed snapshot (no measuring)",
    )
    parser.add_argument("--requests", type=int, default=REQUESTS)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "persist_lab.json")
    args = parser.parse_args(argv)

    cpu_count = os.cpu_count() or 1
    try:
        if args.report:
            snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            cells = cells_from_snapshot(snapshot)
            result = snapshot["interpretation"]
            REPORT_PATH.write_text(
                build_report(cells, result, snapshot.get("cpu_count", cpu_count)),
                encoding="utf-8",
            )
            print(f"report rebuilt from {SNAPSHOT_PATH}: {REPORT_PATH}")
            return 0

        if args.write:
            scenarios: list[Scenario] = []
            for _ in range(max(1, args.repeats)):
                for mode in ARMS:
                    for workers in WORKERS:
                        sc = run_scenario(workers, CONCURRENCY, args.requests, mode=mode)
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
            print(
                f"verdict: {result['verdict']}  "
                f"write cost 1w={result['write_cost_1w']}x  "
                f"async recovery 1w={result['async_recovery_1w']}x  "
                f"lost={result['lost_writes']}"
            )
            print(f"snapshot: {SNAPSHOT_PATH}")
            print(f"report: {REPORT_PATH}")
            return 0

        arms = {
            mode: run_scenario(1, 4, CHECK_REQUESTS, mode=mode) for mode in ARMS
        }
        for sc in arms.values():
            print_summary(sc)
        problems = check_problems(arms["off"], arms["sync"], arms["async"])
        if problems:
            print("PERSIST-LAB GATE FAILED:")
            for problem in problems:
                print(f"  - {problem}")
        elif args.check:
            print("persist-lab gate: OK")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "check": {mode: sc.metrics() for mode, sc in arms.items()},
                    "problems": problems,
                },
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        return 1 if problems else 0
    except Exception as exc:  # noqa: BLE001 - a failed service is a gate failure
        print(f"PERSIST-LAB GATE FAILED: could not run the service: "
              f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
