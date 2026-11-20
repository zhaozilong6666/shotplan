"""Does the storage-init retry actually survive repeated 4-worker cold starts?

One clean `http_load --write` after the retry is not evidence: the original 503
appeared roughly once in a thousand requests. This lab repeats the *startup*
many times on a fresh database, which is where the SQLite schema-creation race
lives, and sends a small burst as soon as the server is ready.

Usage:
    python -m evals.startup_race_lab --write   # 20 rounds, report
    python -m evals.startup_race_lab --check   # 5 rounds, exits 1 on any error
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals.http_load import one, server, total_runs

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "startup-race.md"


def run_round(workers: int, requests: int) -> dict:
    with tempfile.TemporaryDirectory(
        prefix="shotplan-race-", ignore_cleanup_errors=True
    ) as tmp:
        db_path = Path(tmp) / "runs.db"
        with server(workers, db_path) as (base_url, startup_s):
            import httpx

            limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
            with httpx.Client(
                base_url=base_url, timeout=30.0, limits=limits, trust_env=False
            ) as client:
                before = total_runs(client)
                with ThreadPoolExecutor(max_workers=min(8, requests)) as pool:
                    results = list(pool.map(lambda i: one(client, i), range(requests)))
                after = total_runs(client)
            ok = sum(result.kind == "ok" for result in results)
            return {
                "startup_s": round(startup_s, 2),
                "ok": ok,
                "requests": requests,
                "errors": [r.detail for r in results if r.kind != "ok"],
                "http_503": sum(r.detail.startswith("HTTP 503") for r in results),
                "lost_writes": max(0, ok - (after - before)),
            }


def run(rounds: int, workers: int, requests: int) -> list[dict]:
    rows = []
    for index in range(1, rounds + 1):
        try:
            row = run_round(workers, requests)
        except Exception as exc:  # noqa: BLE001 - a startup failure is the result
            row = {
                "startup_s": 0.0,
                "ok": 0,
                "requests": requests,
                "errors": [f"startup: {type(exc).__name__}: {exc}"],
                "http_503": 0,
                "lost_writes": 0,
            }
        row["round"] = index
        rows.append(row)
        print(
            f"round {index}/{rounds}: startup={row['startup_s']}s "
            f"ok={row['ok']}/{row['requests']} errors={len(row['errors'])} "
            f"503={row['http_503']} lost={row['lost_writes']}"
        )
    return rows


def report(rows: list[dict], workers: int, requests: int) -> str:
    failures = sum(len(row["errors"]) for row in rows)
    http_503 = sum(row["http_503"] for row in rows)
    lost = sum(row["lost_writes"] for row in rows)
    starts = [row["startup_s"] for row in rows if row["startup_s"]]
    lines = [
        "> 由 `python -m evals.startup_race_lab --write` 生成，请勿手工编辑。",
        "",
        "# 4 worker 冷启动竞争复测",
        "",
        f"{len(rows)} 轮，每轮全新临时库、启动 {workers} 个 uvicorn worker，"
        f"就绪后并发 {requests} 个 `POST /api/plan`。",
        "",
        "## 结论",
        "",
        f"- 启动失败 / HTTP 错误：**{failures}**",
        f"- 其中 HTTP 503：**{http_503}**",
        f"- 丢写：**{lost}**",
        f"- 启动耗时中位数：**{statistics.median(starts):.2f}s**"
        if starts
        else "- 启动耗时：无可用样本",
        "",
        "## 每轮",
        "",
        "| 轮 | 启动 (s) | 成功 | 503 | 丢写 | 错误 |",
        "| ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['round']} | {row['startup_s']} | {row['ok']}/{requests} "
            f"| {row['http_503']} | {row['lost_writes']} "
            f"| {'；'.join(row['errors'])[:160] if row['errors'] else '—'} |"
        )
    lines += [
        "",
        "## 边界",
        "",
        "这只验证「启动竞争」这一条路径，不验证长稳、并发写吞吐或真实模型延迟；"
        "轮数再多也不能证明 503 的概率为零，只能说明在这台机器、这个负载下没复现。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="4-worker cold-start race lab")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rows = run(args.rounds, args.workers, args.requests)
    text = report(rows, args.workers, args.requests)
    if args.write:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"report: {REPORT}")
    failures = sum(len(row["errors"]) for row in rows)
    lost = sum(row["lost_writes"] for row in rows)
    if args.check and (failures or lost):
        print(f"STARTUP RACE FAILED: errors={failures} lost={lost}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
