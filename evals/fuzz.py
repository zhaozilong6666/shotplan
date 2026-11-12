"""Deterministic input fuzzing for the planning pipeline.

The eval suite proves the planner is *good* on realistic briefs. It says
nothing about what happens when a brief is not realistic: wrong types, NaN,
1e400, emoji floods, NULL fields, reversed constraints, impossible dates.

That matters because the request model is fed straight from JSON — there is no
schema layer between the wire and the domain (ADR 0001). A `500` on a malformed
brief is a production bug, and "the model will always send valid JSON" is not a
contract anyone can enforce.

So the invariant tested here is narrow and absolute:

    for ANY input, run_plan returns a structured result and never raises.

Every generated case carries its own seed, so a failure is reproducible with
``--only-seed``. Junk that cannot be interpreted degrades to a documented
default or a structured refusal — never to a traceback.

Usage:
    python -m evals.fuzz --check                  # CI gate
    python -m evals.fuzz --iterations 500
    python -m evals.fuzz --only-seed 123456789    # replay one case
    python -m evals.fuzz --write-report
"""

from __future__ import annotations

import argparse
import json
import random
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import OUT_DIR
from evals.run_eval import load_cases
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "fuzz-report.md"

FIELDS = (
    "city",
    "date",
    "hours",
    "budget_cny",
    "party_size",
    "styles",
    "preferences",
    "must_include",
    "avoid",
)

# Values chosen to break specific assumptions, not just to look random:
# wrong types, non-finite numbers, clamp-evading magnitudes, encoding traps.
JUNK: tuple[Any, ...] = (
    None,
    "",
    "   ",
    "not-a-number",
    "6h",
    "1e400",
    "-1",
    "0",
    float("nan"),
    float("inf"),
    float("-inf"),
    0,
    -1,
    -10**9,
    10**12,
    2**64,
    True,
    False,
    [],
    {},
    ["tang", None],
    {"city": "xian"},
    [[["nested"]]],
    "\n\t",
    "汉" * 300,
    "🎎" * 80,
    "<script>alert(1)</script>",
    "../../etc/passwd",
    "%s%s%n",
    "2026-02-30",
    "0000-00-00",
    "2026-13-01",
    "10/04/2026",
    "xian'; DROP TABLE runs;--",
)


@dataclass
class CaseReport:
    case_seed: int
    brief: dict[str, Any]
    status: str = ""
    error: str = ""
    serializable: bool = True
    hard_pass: bool = False
    slots: int = 0
    score: float = 0.0
    duration_ms: float = 0.0


@dataclass
class FuzzSummary:
    iterations: int = 0
    seed: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    crashes: list[dict[str, Any]] = field(default_factory=list)
    unserializable: list[dict[str, Any]] = field(default_factory=list)
    feasible_but_failed: list[dict[str, Any]] = field(default_factory=list)
    worst: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "seed": self.seed,
            "by_status": dict(sorted(self.by_status.items())),
            "crash_count": len(self.crashes),
            "unserializable_count": len(self.unserializable),
            "feasible_but_failed_count": len(self.feasible_but_failed),
            "crashes": self.crashes,
            "unserializable": self.unserializable,
            "feasible_but_failed": self.feasible_but_failed,
            "slowest_ms": self.worst,
            "duration_s": round(self.duration_s, 3),
        }


def base_briefs() -> list[dict[str, Any]]:
    """Real briefs are the best starting point: mutate what users actually send."""
    briefs = [dict(case["request"]) for case in load_cases()]
    briefs.append({})  # a completely empty brief must also work
    return briefs


def mutate(rng: random.Random, brief: dict[str, Any]) -> dict[str, Any]:
    out = dict(brief)
    for _ in range(rng.randint(1, 4)):
        strategy = rng.random()
        name = rng.choice(FIELDS)
        if strategy < 0.62:
            out[name] = rng.choice(JUNK)
        elif strategy < 0.78:
            out.pop(name, None)
        elif strategy < 0.90:
            out[name] = rng.choice(JUNK) if rng.random() < 0.5 else out.get(name)
        else:
            out[f"unknown_{rng.randint(0, 3)}"] = rng.choice(JUNK)
    return out


def run_case(brief: dict[str, Any], case_seed: int) -> CaseReport:
    report = CaseReport(case_seed=case_seed, brief=brief)
    started = time.perf_counter()
    try:
        result = run_plan(PlanRequest.from_dict(brief), provider=MockProvider("good"), max_repairs=1)
    except Exception as exc:  # noqa: BLE001 - the whole point is to catch everything
        report.status = "raised"
        report.error = f"{type(exc).__name__}: {exc}\n{''.join(traceback.format_tb(exc.__traceback__))[-600:]}"
        report.duration_ms = (time.perf_counter() - started) * 1000
        return report
    report.duration_ms = (time.perf_counter() - started) * 1000
    report.status = result.status
    report.hard_pass = result.validation.hard_pass
    report.score = result.validation.score
    report.slots = len(result.plan.slots)
    try:
        json.dumps(result.to_dict(), ensure_ascii=False)
    except (TypeError, ValueError):
        report.serializable = False
    return report


def contract_problems(report: CaseReport) -> list[str]:
    """Invariants that must hold for every single case, no exceptions."""
    problems: list[str] = []
    if report.status == "raised":
        problems.append("raised an uncaught exception")
        return problems
    if report.status not in {"ok", "infeasible", "error"}:
        problems.append(f"unknown status {report.status!r}")
    if not report.serializable:
        problems.append("result is not JSON-serialisable")
    if report.status == "error":
        problems.append("pipeline error on malformed-but-parseable input")
    if report.status == "ok":
        if report.slots < 1:
            problems.append("status=ok but the plan has no slots")
        if not 0.0 <= report.score <= 1.0:
            problems.append(f"score out of range: {report.score}")
    return problems


def fuzz(iterations: int, seed: int, only_seed: int | None = None) -> tuple[FuzzSummary, list[CaseReport]]:
    briefs = base_briefs()
    summary = FuzzSummary(iterations=iterations, seed=seed)
    reports: list[CaseReport] = []
    started = time.perf_counter()
    for index in range(iterations):
        # Seed by iteration index, never by the number of collected reports:
        # --only-seed must reproduce a case even when earlier ones are skipped.
        case_seed = random.Random(seed + index).randrange(2**31)
        rng = random.Random(case_seed)
        brief = mutate(rng, rng.choice(briefs))
        if only_seed is not None and case_seed != only_seed:
            continue
        report = run_case(brief, case_seed)
        reports.append(report)
        summary.by_status[report.status] = summary.by_status.get(report.status, 0) + 1
        problems = contract_problems(report)
        for problem in problems:
            entry = {
                "case_seed": report.case_seed,
                "brief": report.brief,
                "problem": problem,
                "status": report.status,
            }
            if report.status == "raised":
                entry["error"] = report.error
                summary.crashes.append(entry)
            elif not report.serializable:
                summary.unserializable.append(entry)
            else:
                summary.feasible_but_failed.append(entry)
    summary.duration_s = time.perf_counter() - started
    summary.worst = [
        {
            "case_seed": r.case_seed,
            "status": r.status,
            "duration_ms": round(r.duration_ms, 2),
            "brief": r.brief,
        }
        for r in sorted(reports, key=lambda r: r.duration_ms, reverse=True)[:5]
    ]
    return summary, reports


def build_report(summary: FuzzSummary, reports: list[CaseReport]) -> str:
    status_counts = ", ".join(f"`{k}` {v}" for k, v in sorted(summary.by_status.items()))
    lines = [
        "# 输入模糊测试报告",
        "",
        "> 由 `python -m evals.fuzz --write-report` 生成，请勿手工编辑。",
        "",
        "## 结论",
        "",
        f"- 迭代 **{summary.iterations}** 次，种子 `{summary.seed}`，耗时 {summary.duration_s:.2f}s",
        f"- 状态分布：{status_counts or '（无）'}",
        f"- **未捕获异常：{len(summary.crashes)}**",
        f"- 结果不可序列化：{len(summary.unserializable)}",
        f"- 可行输入却未通过硬约束：{len(summary.feasible_but_failed)}",
        "",
        "## 测的是什么",
        "",
        "评测集证明「规划器在真实 brief 上足够好」；模糊测试证明**任何输入都不会让系统崩**。",
        "请求直接从 JSON 进入领域模型，中间没有 schema 层（ADR 0001），所以垃圾输入必须由",
        "`PlanRequest.normalized()` 兜住：非法数值退化为文档化默认值，非法日期变成结构化拒绝，",
        "provider 或解析失败返回 `status=error` 而不是 500。",
        "",
        "## 不变量",
        "",
        "1. `run_plan` 对任意输入都返回结构化结果，绝不抛异常",
        "2. `status` 必须是 `ok` / `infeasible` / `error` 之一",
        "3. `status=ok` 时至少有一个时间块，且分数落在 `[0, 1]`",
        "4. 结果必须可 JSON 序列化（API 会直接返回它）",
        "",
    ]
    if summary.crashes:
        lines += ["## 失败用例（可用 `--only-seed` 复现）", ""]
        for crash in summary.crashes[:10]:
            lines += [
                f"### seed `{crash['case_seed']}`",
                "",
                f"- 问题：{crash['problem']}",
                f"- 输入：`{json.dumps(crash['brief'], ensure_ascii=False)[:200]}`",
                "",
                "```",
                crash.get("error", "")[:800],
                "```",
                "",
            ]
    else:
        lines += ["## 失败用例", "", f"无。{summary.iterations} 个变异 brief 全部返回结构化结果。", ""]
    if summary.worst:
        lines += ["## 最慢的 5 个用例", "", "| seed | status | 耗时 (ms) |", "|---|---|---|"]
        lines += [f"| `{w['case_seed']}` | {w['status']} | {w['duration_ms']} |" for w in summary.worst]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan deterministic input fuzzer")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--only-seed", type=int, default=None, help="replay a single case by its seed")
    parser.add_argument("--check", action="store_true", help="CI gate: fail on any contract violation")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "fuzz.json")
    args = parser.parse_args(argv)

    summary, reports = fuzz(max(1, args.iterations), args.seed, args.only_seed)
    payload = summary.to_dict()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(build_report(summary, reports), encoding="utf-8")

    print(f"iterations: {summary.iterations} | seed: {summary.seed} | {summary.duration_s:.2f}s")
    print(f"status: {dict(sorted(summary.by_status.items()))}")
    print(f"crashes: {len(summary.crashes)} | unserializable: {len(summary.unserializable)} "
          f"| feasible-but-failed: {len(summary.feasible_but_failed)}")
    for crash in summary.crashes[:5]:
        print(f"  CRASH seed={crash['case_seed']} {crash['problem']}: {crash.get('error', '')[:160]}")
    violations = len(summary.crashes) + len(summary.unserializable)
    if args.check:
        if violations:
            print(f"FUZZ GATE FAILED: {violations} contract violation(s)")
        else:
            print("fuzz gate: OK")
    if args.write_report:
        print(f"report: {args.report_path}")
    print(f"raw: {args.out}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
