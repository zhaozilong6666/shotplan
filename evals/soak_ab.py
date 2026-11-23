"""Interleaved 1-worker vs 4-worker soak under the same write pressure.

Two 120-minute runs produced two single observations: 4 workers -> 20
``ReadTimeout`` errors, 1 worker -> 0. Single runs cannot separate worker count
from time-of-day drift, and P95 hides a one-in-seven-thousand freeze. This lab
alternates the arms inside one session (1/4, then 4/1) and keeps the tail
metrics explicit: P99.9, max and the count of requests slower than 1s / 5s.

It is a provocation test, not a capacity benchmark: higher write pressure makes
a lock-convoy easier to see, but a 40-minute window still cannot disprove a
two-hour tail.

Usage:
    python -m evals.soak_ab --minutes 10 --write
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from evals.soak import run

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "soak-ab.md"
SNAPSHOT = ROOT / "evals" / "soak_ab.json"

ARM_KEYS = (
    "minutes", "workers", "concurrency", "interval_s", "requests", "ok", "errors",
    "restarts", "rows", "lost", "rps", "p50_ms", "p95_ms", "p99_ms", "p999_ms",
    "max_ms", "slow_1s", "slow_5s", "error_samples",
)


def _aggregate(arms: list[dict[str, Any]], workers: int) -> dict[str, Any]:
    rows = [row for row in arms if row["workers"] == workers]
    return {
        "workers": workers,
        "rounds": len(rows),
        "requests": sum(row["requests"] for row in rows),
        "errors": sum(row["errors"] for row in rows),
        "lost": sum(row["lost"] for row in rows),
        "slow_1s": sum(row["slow_1s"] for row in rows),
        "slow_5s": sum(row["slow_5s"] for row in rows),
        "rps_median": round(statistics.median([row["rps"] for row in rows]), 2),
        "p95_median": round(statistics.median([row["p95_ms"] for row in rows]), 2),
        "p999_worst": round(max(row["p999_ms"] for row in rows), 2),
        "max_worst": round(max(row["max_ms"] for row in rows), 2),
    }


def verdict(arms: list[dict[str, Any]], workers: list[int]) -> str:
    """Say only what this batch can support, round by round.

    A tail that appears in one round and vanishes in the next is not a property
    of the worker count. This mirrors evals/worker_lab.py: first ask whether the
    phenomenon reproduces, then attribute it.
    """
    low, high = min(workers), max(workers)
    # A timeout also lands in the >5s bucket, so summing the two would count the
    # same event twice. The larger counter is a lower bound on bad requests.
    low_rounds = [
        max(row["errors"], row["slow_5s"]) for row in arms if row["workers"] == low
    ]
    high_rounds = [
        max(row["errors"], row["slow_5s"]) for row in arms if row["workers"] == high
    ]
    low_total, high_total = sum(low_rounds), sum(high_rounds)
    if low_total == 0 and high_total == 0:
        return (
            "本批未复现 >5s 长尾：两臂在观察窗口内都无法区分。"
            "这不能推翻 120 分钟运行里的单次观测，也不能归因给 worker 数。"
        )
    if low_total > high_total:
        return (
            "方向不一致：低 worker 数反而出现更多长尾，本批不构成多进程归因的证据。"
        )
    reproduced = sum(1 for bad in high_rounds if bad > 0)
    if reproduced == len(high_rounds) and low_total == 0:
        return (
            "本批数据支持「多进程共享一个 SQLite 文件是长尾来源」："
            f"{high} worker 每轮都有异常/超 5s（共 {high_total} 次），"
            f"{low} worker 为 0。"
        )
    return (
        f"部分复现：{high} worker 的长尾只出现在 {reproduced}/{len(high_rounds)} 轮"
        f"（共 {high_total} 次），{low} worker 为 {low_total} 次。"
        "现象没有逐轮复现，本批不足以把 worker 数定为原因。"
    )


def render(
    arms: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    workers: list[int],
    args: argparse.Namespace,
) -> str:
    lines = [
        "> 由 `python -m evals.soak_ab --write` 生成，请勿手工编辑。",
        "",
        "# 1 worker vs 4 worker：交替长跑",
        "",
        f"每臂 {args.minutes:g} 分钟，`batch` 持久化，并发 {args.concurrency}，"
        f"每 {args.interval:g}s 一个请求；共 {args.rounds} 轮，轮内顺序交替。",
        "两条臂使用全新数据库、同一份 mock 代码，唯一的差别是 worker 数。",
        "",
        "## 每臂结果",
        "",
        "| 轮 | worker | 请求 | 错误 | 丢写 | 吞吐 /s | P99 (ms) | P99.9 (ms) | 最大 (ms) | >1s | >5s |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in arms:
        lines.append(
            f"| {row['round']} | {row['workers']} | {row['requests']} | {row['errors']} "
            f"| {row['lost']} | {row['rps']} | {row['p99_ms']} | {row['p999_ms']} "
            f"| {row['max_ms']} | {row['slow_1s']} | {row['slow_5s']} |"
        )
    lines += [
        "",
        "## 汇总",
        "",
        "| worker | 轮数 | 请求 | 错误 | 丢写 | 吞吐中位数 /s | P95 中位数 | 最差 P99.9 | 最差最大 | >1s | >5s |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        lines.append(
            f"| {row['workers']} | {row['rounds']} | {row['requests']} | {row['errors']} "
            f"| {row['lost']} | {row['rps_median']} | {row['p95_median']} "
            f"| {row['p999_worst']} | {row['max_worst']} | {row['slow_1s']} | {row['slow_5s']} |"
        )
    lines += [
        "",
        "## 判定",
        "",
        verdict(arms, workers),
        "",
        "## 边界",
        "",
        f"- 观察窗口共 {args.minutes * args.rounds * len(workers):g} 分钟；稀有事件的样本量仍然很小，"
        "结论是「本批数据是否支持」，不是概率估计。",
        "- 高写入压力是为了更快暴露锁竞争，吞吐数字不能外推到默认负载。",
        "- 单机 loopback、mock provider、Windows；没有 TLS、反向代理与真实模型延迟。",
        "- 长尾可能来自 SQLite、Windows 文件系统或客户端连接池；本实验只区分「与 worker 数是否相关」。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="interleaved 1w/4w soak")
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 4])
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--rerender",
        action="store_true",
        help="rewrite the report from evals/soak_ab.json without re-running the soak",
    )
    args = parser.parse_args(argv)

    if args.rerender:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        ns = argparse.Namespace(
            minutes=data["minutes"],
            concurrency=data["concurrency"],
            interval=data["interval_s"],
            rounds=data["rounds"],
        )
        text = render(data["arms"], data["summary"], data["workers"], ns)
        data["verdict"] = verdict(data["arms"], data["workers"])
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        SNAPSHOT.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"re-rendered: {REPORT}")
        return 0

    arms: list[dict[str, Any]] = []
    for round_no in range(1, args.rounds + 1):
        order = args.workers if round_no % 2 else list(reversed(args.workers))
        for workers in order:
            print(f"round {round_no}: {workers} worker ...", flush=True)
            result = run(
                args.minutes, workers, "batch", args.interval, 0.0, args.concurrency
            )
            arm = {"round": round_no, **{key: result[key] for key in ARM_KEYS}}
            arms.append(arm)
            print(
                f"  req={arm['requests']} ok={arm['ok']} err={arm['errors']} "
                f"lost={arm['lost']} rps={arm['rps']} p99={arm['p99_ms']} "
                f"p999={arm['p999_ms']} max={arm['max_ms']} "
                f"slow5={arm['slow_5s']}",
                flush=True,
            )

    summaries = [_aggregate(arms, workers) for workers in args.workers]
    text = render(arms, summaries, args.workers, args)
    print(text)
    if args.write:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        SNAPSHOT.write_text(
            json.dumps(
                {
                    "minutes": args.minutes,
                    "concurrency": args.concurrency,
                    "interval_s": args.interval,
                    "rounds": args.rounds,
                    "workers": args.workers,
                    "arms": arms,
                    "summary": summaries,
                    "verdict": verdict(arms, args.workers),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"report: {REPORT}")
    return 0 if all(arm["lost"] == 0 for arm in arms) else 1


if __name__ == "__main__":
    raise SystemExit(main())
