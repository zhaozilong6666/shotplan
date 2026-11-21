"""Measure `batch` persistence: one commit for N requests, each still waits.

`sync` commits per request; `async` returns before the commit; `batch` waits for
a shared commit. The question is whether sharing the commit keeps sync's
durability promise while removing most of its cost, at 1 and 4 workers.

Usage:
    python -m evals.batch_lab --write
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from evals.http_load import run_scenario

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "batch-write.md"
MODES = ("sync", "async", "batch")


def run(
    requests: int, concurrency: int, workers_levels: list[int]
) -> dict[int, list[dict[str, Any]]]:
    results: dict[int, list[dict[str, Any]]] = {}
    for workers in workers_levels:
        rows = []
        for mode in MODES:
            scenario = run_scenario(workers, concurrency, requests, mode=mode)
            metrics = scenario.metrics()
            metrics["mode"] = mode
            rows.append(metrics)
            print(
                f"{workers}w {mode}: rps={metrics['rps']} p95={metrics['p95_ms']}ms "
                f"errors={metrics['errors']} lost={metrics['lost_writes']} "
                f"persisted={metrics['runs_delta']}/{metrics['ok']}"
            )
        results[workers] = rows
    return results


def report(
    results: dict[int, list[dict[str, Any]]], requests: int, concurrency: int
) -> str:
    lines = [
        "> 由 `python -m evals.batch_lab --write` 生成，请勿手工编辑。",
        "",
        "# 批量提交：一次 commit，N 个请求都等到落库",
        "",
        f"并发 {concurrency}、{requests} 请求；同一份 mock 代码只改 "
        "`SHOTPLAN_PERSIST_MODE`，分别跑 1 / 4 worker。",
        "",
        "## 结果",
        "",
    ]
    summary = []
    for workers, rows in results.items():
        by_mode = {row["mode"]: row for row in rows}
        sync = by_mode["sync"]
        batch = by_mode["batch"]
        speedup = batch["rps"] / sync["rps"] if sync["rps"] else 0.0
        p95_ratio = batch["p95_ms"] / sync["p95_ms"] if sync["p95_ms"] else 0.0
        summary.append((workers, speedup, p95_ratio, batch["lost_writes"]))
        lines += [
            f"### {workers} worker",
            "",
            "| 模式 | 吞吐 (/s) | P95 (ms) | 错误 | 落库 | 丢写 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in rows:
            lines.append(
                f"| `{row['mode']}` | {row['rps']} | {row['p95_ms']} | {row['errors']} "
                f"| {row['runs_delta']}/{row['ok']} | {row['lost_writes']} |"
            )
        lines += [
            "",
            f"`batch` 相对 `sync`：**{speedup:.2f}×** 吞吐，P95 为 sync 的 "
            f"**{p95_ratio:.2f}×**，丢写 {batch['lost_writes']}。",
            "",
        ]
    lines += [
        "## 结论",
        "",
    ]
    for workers, speedup, p95_ratio, lost in summary:
        lines.append(
            f"- {workers} worker：batch **{speedup:.2f}×** sync，P95 **{p95_ratio:.2f}×**，"
            f"丢写 **{lost}**。"
        )
    lines += [
        "",
        "## 语义",
        "",
        "- `sync`：每个请求一次 commit，返回即落库。",
        "- `async`：入队即返回，硬杀会丢队列里的行（已有测量）。",
        "- `batch`：入队后**等一次共享 commit** 再返回；并发越高，摊薄越明显。"
        "队列满/关闭时退化为同步写，仍然等到落库。",
        "",
        "## 边界",
        "",
        "单请求、并发 1 时 batch 没有可合并对象，延迟与 sync 接近甚至略高；"
        "它是给并发服务的，不是给单请求提速的。吞吐数字机器相关，不进 CI 门禁。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="batch persistence lab")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 4])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    results = run(args.requests, args.concurrency, args.workers)
    text = report(results, args.requests, args.concurrency)
    if args.write:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
