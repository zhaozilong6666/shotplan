"""Does `batch` really commit before the response? Kill the process and count.

`sync` and `batch` both promise "returned means stored". For `batch` this is the
whole point: it shares a commit but must still wait for it. This lab sends N
requests, lets the `server()` context kill uvicorn with `taskkill /F` on exit,
then opens the database and counts rows. A single missing row refutes the mode.

Windows-only hard kill: on POSIX `_stop` sends SIGTERM, which uvicorn may drain.

Usage:
    python -m evals.batch_kill_lab --write
    python -m evals.batch_kill_lab --check   # one round, exits 1 on loss
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import httpx

from evals.http_load import BRIEFS, server
from shotplan.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "batch-kill.md"


def run_round(requests: int) -> dict:
    with tempfile.TemporaryDirectory(
        prefix="shotplan-batch-kill-", ignore_cleanup_errors=True
    ) as tmp:
        db_path = Path(tmp) / "runs.db"
        returned = 0
        with server(1, db_path, mode="batch") as (base_url, _startup):
            with httpx.Client(base_url=base_url, timeout=60.0, trust_env=False) as client:
                for index in range(requests):
                    response = client.post("/api/plan", json=BRIEFS[index % len(BRIEFS)])
                    response.raise_for_status()
                    returned += 1
        # The context manager just hard-killed uvicorn (taskkill /F on Windows).
        rows = RunStore(db_path).summary()["total_runs"]
        return {"returned": returned, "rows": rows, "lost": max(0, returned - rows)}


def report(rows: list[dict]) -> str:
    lost = sum(row["lost"] for row in rows)
    lines = [
        "> 由 `python -m evals.batch_kill_lab --write` 生成，请勿手工编辑。",
        "",
        "# batch 硬杀：返回即落库，杀进程也不丢",
        "",
        f"{len(rows)} 轮；每轮全新库，batch 模式发完请求后由 `server()` 退出时 "
        "`taskkill /F /T` 硬杀 uvicorn，再重新打开库数行。",
        "",
        "## 结果",
        "",
        "| 轮 | 返回 200 | 硬杀后行数 | 丢写 |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"| {index} | {row['returned']} | {row['rows']} | {row['lost']} |"
        )
    lines += [
        "",
        f"总丢写：**{lost}**。",
        "",
        "## 语义",
        "",
        "- `batch` 与 `sync` 都等到 commit 才返回；硬杀发生在返回之后，所以不应丢行。",
        "- `async` 不等待 commit，硬杀会丢队列里的行；这条差异已有测量（tests/test_write_behind.py）。",
        "- 本测试只覆盖 Windows `taskkill /F`；POSIX 的 SIGTERM 可能被 uvicorn 优雅处理，"
        "不能拿来当硬杀证据。",
        "",
        "## 边界",
        "",
        "只验证「已返回的请求」；请求在途中被杀的语义不在本测试里。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="batch hard-kill durability lab")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rows = []
    for index in range(1, args.rounds + 1):
        row = run_round(args.requests)
        rows.append(row)
        print(
            f"round {index}/{args.rounds}: returned={row['returned']} "
            f"rows={row['rows']} lost={row['lost']}"
        )
    if args.write:
        REPORT.write_text(report(rows), encoding="utf-8", newline="\n")
        print(f"report: {REPORT}")
    lost = sum(row["lost"] for row in rows)
    if args.check and lost:
        print(f"BATCH KILL FAILED: lost={lost}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
