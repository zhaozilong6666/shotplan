"""Which gates are flaky on this machine, measured instead of guessed.

One CI run failed at `make gates` and the next, with strictly more content,
passed. The honest response is not to widen a threshold or add a retry -- both
hide the signal -- but to find out which gate actually wobbles.

Only the timing- and load-dependent gates are repeated here. The rest are pure
functions of the repo (parsers, generators, renderers) and repeating them would
measure the disk. Run it when a gate fails without a reason; it is a diagnostic,
not part of `make gates`, because 3x on ten gates is a minute of CI spent proving
nothing on a normal day.

Whatever it reports is about *this* machine. A gate that is stable here can still
wobble on a shared CI runner with fewer cores; that difference is itself the
finding, and it is why the report says where it was measured.
"""

from __future__ import annotations

import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "gate-stability.md"

CANDIDATES = (
    ("evals.benchmark", ["--check"]),
    ("evals.loadtest", ["--check"]),
    ("evals.http_load", ["--check"]),
    ("evals.worker_lab", ["--check"]),
    ("evals.persist_lab", ["--check"]),
    ("evals.fault_injection", ["--check"]),
    ("evals.slo_baseline", ["--check"]),
    ("evals.scaling", ["--check", "--requests", "300"]),
)

RUNS = 3


def run_once(module: str, args: list[str]) -> tuple[bool, float, str]:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - started
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    return proc.returncode == 0, elapsed, tail[-1] if tail else ""


def measure() -> list[dict]:
    rows = []
    for module, args in CANDIDATES:
        outcomes, timings, messages = [], [], []
        for _ in range(RUNS):
            ok, elapsed, message = run_once(module, args)
            outcomes.append(ok)
            timings.append(elapsed)
            if not ok:
                messages.append(message)
        rows.append(
            {
                "module": module,
                "args": " ".join(args),
                "runs": len(outcomes),
                "passed": sum(outcomes),
                "median": statistics.median(timings),
                "worst": max(timings),
                "failures": messages,
            }
        )
    return rows


def render(rows: list[dict]) -> str:
    flaky = [row for row in rows if row["passed"] < row["runs"]]
    stable = [row for row in rows if row["passed"] == row["runs"]]
    slowest = max(rows, key=lambda row: row["worst"]) if rows else None

    lines = [
        "> 由 `python -m evals.gate_stability --write` 生成，请勿手工编辑。",
        "",
        "# 门禁稳定性实测",
        "",
        f"一次 CI 在 `make gates` 失败，而下一版内容只多不少却全过。为了不再靠重跑碰运气，"
        f"把**依赖时序与负载**的 {len(rows)} 道门禁各连跑 {RUNS} 次，记录通过率与耗时。"
        "纯函数型门禁（解析器、生成器、渲染器）没有重复测量——重复它们只是在测磁盘。",
        "",
        "| 门禁 | 次数 | 通过 | 中位耗时 | 最慢 | 失败信息 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        message = row["failures"][0][:60] if row["failures"] else "—"
        lines.append(
            f"| `{row['module']} {row['args']}` | {row['runs']} | {row['passed']} "
            f"| {row['median']:.2f}s | {row['worst']:.2f}s | {message} |"
        )
    lines.append("")

    if flaky:
        lines += [
            f"**有 {len(flaky)} 道在本机就不稳定**，它们是下次 CI 变红的第一嫌疑人：",
            "",
        ]
        for row in flaky:
            lines.append(
                f"- `{row['module']}`：{row['runs']} 次里失败 {row['runs'] - row['passed']} 次"
                f"（{row['failures'][0][:80] if row['failures'] else ''}）"
            )
        lines.append("")
    else:
        lines += [
            f"**{len(stable)} 道在本机全部通过**。这说明那次 CI 失败要么发生在别的门禁上，"
            "要么是 CI 运行器特有的（核数更少、共享负载）。两种情况都不该靠放宽阈值处理："
            "如果是前者，下次失败时 `evals/out/gates.log` 会直接指出是哪一道；"
            "如果是后者，应该按运行器能力分档，而不是让所有环境迁就最弱的那个。",
            "",
        ]

    if slowest:
        lines += [
            f"最慢的一道是 `{slowest['module']}`，最慢一次 {slowest['worst']:.2f}s。"
            "它决定了门禁矩阵的墙钟下限，值得知道。",
            "",
        ]

    lines += [
        "## 这是在哪测的",
        "",
        f"本次测量：{platform.system()} {platform.release()}，{os.cpu_count()} 核，"
        f"Python {platform.python_version()}。",
        "",
        "本报告只描述执行它的那台机器。同一道门禁在核数更少、或有其他任务争抢 CPU 的运行器上"
        "可能表现不同——那正是「本机稳定、CI 抖动」的来源。所以读这份报告要先看它是在哪跑的，"
        "而不是只看通过率。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = measure()
    text = render(rows)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    flaky = [row for row in rows if row["passed"] < row["runs"]]
    print(f"gate stability: {len(rows)} gates x {RUNS}, {len(flaky)} unstable")
    for row in flaky:
        print(f"  UNSTABLE {row['module']} {row['passed']}/{row['runs']}")
    if not sys.argv[1:]:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
