"""Gate-sensitivity lab: sabotage the system on purpose and prove CI notices.

Why this exists
---------------
An 80/80 green eval run is not evidence that the gate works. It only says the
current code passes the current checks. To test the CHECKS themselves we inject
eight controlled mutations at runtime (no source file is ever modified), rerun
the exact same 80 cases through the exact same pipeline, and require the real
``evals.scorers.eval_gate_problems`` to reject every single one.

The lab found a real hole. With only "good must score above a threshold" and
"the control group must not beat the planner", four validator-weakening
mutations slipped through: the naive control plan violates so many rules that
its ``constraints`` score is already clamped at 0, so silently deleting one
hard rule barely moves its average. The fix (see ADR 0009) is a rule-level
coverage floor on the control group, plus a delta floor and a control ceiling.

Everything written to disk is deterministic: no timestamps, ids or timings.
``--check`` regenerates both artifacts and diffs them, so CI cannot pass with
a stale report.

Usage:
    python -m evals.mutation_lab --write
    python -m evals.mutation_lab --check      # CI gate
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evals.run_eval import load_cases, run_suite
from evals.scorers import (
    CONTROL_RULE_MIN_COUNTS,
    MAX_CONTROL_AVG_SCORE,
    MAX_CONTROL_HARD_PASS_RATE,
    MIN_CONTROL_DELTA,
    aggregate,
    eval_gate_problems,
    regression_check,
)
from shotplan import pipeline
from shotplan.agents import MockProvider
from shotplan.agents import mock as mock_module
from shotplan.agents import validator as validator_module
from shotplan.domain.models import ValidationReport
from shotplan.knowledge.tools import KnowledgeTools

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "mutation-report.md"
DEFAULT_SNAPSHOT = ROOT / "evals" / "mutation.json"

# These mirror the defaults in run_eval.main. The lab deliberately calls the
# SAME gate function, so a future change to CI's checks is exercised here too.
MIN_SCORE = 0.80
MIN_HARD_PASS_RATE = 0.90


# ---------------------------------------------------------------- mutations ---
def _planner_no_transfer(stack: ExitStack) -> None:
    """Planner stops inserting travel blocks; validator still demands 20 min."""
    stack.enter_context(patch.object(mock_module, "MIN_TRANSFER_MINUTES", 0))


def _planner_transfer_cost(stack: ExitStack) -> None:
    """Transfer fee inflated so every good plan blows the budget."""
    stack.enter_context(patch.object(mock_module, "TRANSFER_COST_CNY", 5000))


def _planner_no_makeup(stack: ExitStack) -> None:
    """Good drafts silently lose the required makeup block."""
    original = MockProvider._good

    def wrapper(self, req, context, kb, repair_hint):
        payload = original(self, req, context, kb, repair_hint)
        payload["slots"] = [s for s in payload["slots"] if s["activity"] != "makeup"]
        return payload

    stack.enter_context(patch.object(MockProvider, "_good", wrapper))


def _validator_budget_tolerance(stack: ExitStack) -> None:
    stack.enter_context(patch.object(validator_module, "BUDGET_TOLERANCE_CNY", 10**9))


def _validator_no_transfer(stack: ExitStack) -> None:
    stack.enter_context(patch.object(validator_module, "MIN_TRANSFER_MINUTES", 0))


def _validator_no_time_limit(stack: ExitStack) -> None:
    stack.enter_context(patch.object(validator_module, "MAX_OVERRUN_MINUTES", 10**6))


def _universal_location_ids() -> set[str]:
    kb = KnowledgeTools()
    ids: set[str] = set()
    for city in kb.city_ids():
        ids |= kb.location_ids(city)
    # Ids the naive provider hard-codes, including the deliberately invented one.
    ids |= {
        "indoor_studio", "big_wild_goose_pagoda", "datang_furong_garden",
        "nonexistent_palace", "city_wall", "none", "-", "",
    }
    return ids


def _validator_no_grounding(stack: ExitStack) -> None:
    """Pretend every location id exists, including the invented one."""
    known = _universal_location_ids()
    stack.enter_context(
        patch.object(KnowledgeTools, "location_ids", lambda self, city_id: known)
    )


def _always_pass(plan, req, kb=None) -> ValidationReport:
    return ValidationReport(issues=[], scores={}, score=1.0, hard_pass=True)


def _validator_always_pass(stack: ExitStack) -> None:
    """The scariest mutation: a judge that approves everything (CI stays green)."""
    stack.enter_context(patch.object(pipeline, "validate", _always_pass))


@dataclass(frozen=True)
class Mutant:
    key: str
    side: str
    description: str
    apply: Callable[[ExitStack], None]


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "planner/no-transfer", "planner",
        "生成端不再插入转场时间块（校验端仍要求 20 分钟）", _planner_no_transfer,
    ),
    Mutant(
        "planner/transfer-cost-5000", "planner",
        "转场费从 50 元抬到 5000 元", _planner_transfer_cost,
    ),
    Mutant(
        "planner/no-makeup", "planner",
        "生成端删掉必需的妆造时间块", _planner_no_makeup,
    ),
    Mutant(
        "validator/budget-tolerance", "validator",
        "预算容差改成 10^9，BUDGET_EXCEEDED 永不触发", _validator_budget_tolerance,
    ),
    Mutant(
        "validator/no-transfer-rule", "validator",
        "转场下限改成 0，TRANSFER_BUFFER 永不触发", _validator_no_transfer,
    ),
    Mutant(
        "validator/no-time-limit", "validator",
        "时长宽限改成 10^6 分钟，TIME_OVERRUN 永不触发", _validator_no_time_limit,
    ),
    Mutant(
        "validator/no-grounding", "validator",
        "所有地点 id 都视为存在，UNKNOWN_LOCATION 永不触发", _validator_no_grounding,
    ),
    Mutant(
        "validator/always-pass", "validator",
        "校验器直接返回满分且无问题", _validator_always_pass,
    ),
)


# ----------------------------------------------------------------- measuring ---
def measure(cases: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run both providers through the real pipeline, just like run_eval does."""
    return aggregate(run_suite(cases, "good")), aggregate(run_suite(cases, "naive"))


def fingerprint(good: dict[str, Any], naive: dict[str, Any]) -> dict[str, Any]:
    """Only deterministic fields; timings and ids are intentionally excluded."""
    return {
        "good": {
            "case_count": good["case_count"],
            "avg_score": good["avg_score"],
            "hard_pass_rate": good["hard_pass_rate"],
            "cases_passed": good["cases_passed"],
            "cases_failed": good["cases_failed"],
        },
        "naive": {
            "avg_score": naive["avg_score"],
            "hard_pass_rate": naive["hard_pass_rate"],
            "issue_frequency": {
                code: naive["issue_frequency"].get(code, 0)
                for code in sorted(CONTROL_RULE_MIN_COUNTS)
            },
        },
    }


def legacy_gate_problems(good: dict[str, Any], naive: dict[str, Any]) -> list[str]:
    """The gate as it existed before ADR 0009, kept for the before/after count."""
    problems = regression_check(good, MIN_SCORE, MIN_HARD_PASS_RATE)
    if good["avg_score"] <= naive["avg_score"]:
        problems.append("control group scored at or above the planner: measurement is broken")
    return problems


def run_lab(cases: list[dict[str, Any]]) -> dict[str, Any]:
    base_good, base_naive = measure(cases)
    baseline_problems = eval_gate_problems(
        base_good, base_naive, MIN_SCORE, MIN_HARD_PASS_RATE, min_delta=MIN_CONTROL_DELTA
    )
    base_fp = fingerprint(base_good, base_naive)
    base_record = {
        "good_avg": base_good["avg_score"],
        "good_hard_pass_rate": base_good["hard_pass_rate"],
        "cases_failed": base_good["cases_failed"],
        "naive_avg": base_naive["avg_score"],
        "naive_hard_pass_rate": base_naive["hard_pass_rate"],
        "delta": round(base_good["avg_score"] - base_naive["avg_score"], 4),
        "rule_counts": {
            code: base_naive["issue_frequency"].get(code, 0)
            for code in CONTROL_RULE_MIN_COUNTS
        },
    }

    rows: list[dict[str, Any]] = []
    restored_checks: list[bool] = []
    for mutant in MUTANTS:
        with ExitStack() as stack:
            mutant.apply(stack)
            good, naive = measure(cases)
        signals = eval_gate_problems(
            good, naive, MIN_SCORE, MIN_HARD_PASS_RATE, min_delta=MIN_CONTROL_DELTA
        )
        legacy = legacy_gate_problems(good, naive)
        observed = naive["issue_frequency"]
        disabled = sorted(
            code for code, floor in CONTROL_RULE_MIN_COUNTS.items()
            if observed.get(code, 0) < floor
        )
        rows.append({
            "key": mutant.key,
            "side": mutant.side,
            "description": mutant.description,
            "good_avg": good["avg_score"],
            "score_delta": round(good["avg_score"] - base_good["avg_score"], 4),
            "good_hard_pass_rate": good["hard_pass_rate"],
            "cases_failed": good["cases_failed"],
            "naive_avg": naive["avg_score"],
            "delta": round(good["avg_score"] - naive["avg_score"], 4),
            "disabled_rules": disabled,
            "signals": signals,
            "caught": bool(signals),
            "caught_by_legacy_gate": bool(legacy),
        })
        # Rollback is the point: every patch must restore the exact baseline.
        check_good, check_naive = measure(cases)
        restored_checks.append(fingerprint(check_good, check_naive) == base_fp)

    restored = all(restored_checks)
    after_good, after_naive = measure(cases)
    return {
        "case_count": len(cases),
        "mutant_count": len(MUTANTS),
        "caught": sum(row["caught"] for row in rows),
        "survivors": sum(not row["caught"] for row in rows),
        "caught_by_legacy_gate": sum(row["caught_by_legacy_gate"] for row in rows),
        "baseline": base_record,
        "baseline_gate_problems": baseline_problems,
        "restored": restored,
        "restored_checks": len(restored_checks),
        "restored_avg": after_good["avg_score"],
        "restored_hard_pass_rate": after_good["hard_pass_rate"],
        "restored_cases_failed": after_good["cases_failed"],
        "mutants": rows,
    }


# ------------------------------------------------------------------- reporting ---
def build_document(lab: dict[str, Any]) -> str:
    base = lab["baseline"]
    lines = [
        "# 门禁有效性：变异测试",
        "",
        "> 由 `python -m evals.mutation_lab --write` 生成，请勿手工编辑。",
        "> 所有改动都在**进程内注入并回滚**，不会修改仓库里的任何源文件。",
        "",
        "## 为什么需要这一层",
        "",
        f"评测集 {lab['case_count']}/{lab['case_count']} 全绿、均分 {base['good_avg']}，"
        "只能说明「当前代码通过了当前检查」，不能说明**检查本身有效**。"
        "一个永远返回满分的校验器，同样能让 CI 全绿。",
        "",
        f"本实验注入 {lab['mutant_count']} 处受控故障，用同一套用例、同一条流水线重跑，"
        f"要求真实门禁 `evals.scorers.eval_gate_problems` 每一次都拒绝："
        f"**{lab['caught']}/{lab['mutant_count']} 被拦截，存活 {lab['survivors']} 处**。",
        f"作为对照，修复前的旧门禁（均分下限 + 对照组不能更好）只能拦住 "
        f"**{lab['caught_by_legacy_gate']}/{lab['mutant_count']}** 处。",
        "",
        "盲点很具体：故意犯错的对照组同时违反多条硬规则，"
        "`constraints` 维度早就被 clamp 到 0；此时悄悄删掉一条规则，"
        "对照组均分几乎不动，好方案也没变化——旧门禁看不出任何异常。"
        "所以 ADR 0009 给对照组加了**规则级覆盖下限**：每条已知缺陷都必须被报出来。",
        "",
        "## 方法",
        "",
        "1. 从 `evals/cases/*.jsonl` 读入全部用例，good / naive 两个 provider 各跑一遍。",
        "2. 每处变异只改一个模块级常量或一个方法，注入前后共用同一批用例与同一份门禁函数。",
        "3. 门禁函数不是本实验的复制品，而是 `run_eval` 直接调用的那个，"
        "所以改门禁而忘了改实验不会发生。",
        "4. 每轮结束后用 `ExitStack` 回滚，再跑一次基线并逐字段比对；"
        "只有确定性字段参与比对（不含耗时、id）。",
        "",
        "## 结果",
        "",
        "| 变异体 | 改了什么 | good 均分（较基线） | 硬约束通过率 | 失败用例 | naive 均分 | 差值 | 触发的门禁 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in lab["mutants"]:
        signal = row["signals"][0] if row["signals"] else "**未拦截**"
        if len(row["signals"]) > 1:
            signal += f" 等 {len(row['signals'])} 项"
        lines.append(
            f"| `{row['key']}` | {row['description']} | {row['good_avg']:.4f} "
            f"({row['score_delta']:+.4f}) | {row['good_hard_pass_rate']:.0%} | "
            f"{row['cases_failed']} | {row['naive_avg']:.4f} | {row['delta']:.4f} | "
            f"{'拦截' if row['caught'] else '漏过'}：{signal} |"
        )
    lines += [
        "",
        "前三行是**生成端**变坏：分数、通过率、失败用例数直接掉下来，"
        "任何像样的阈值都能拦住。后五行是**校验端**被悄悄削弱："
        "好方案的指标几乎不变，只有对照组的规则覆盖发生了变化。",
        "",
        "## 新增的检查",
        "",
        "| 检查 | 阈值 | 基准实测 | 抓到哪类变异 |",
        "| --- | ---: | ---: | --- |",
        f"| 两组均分差值下限 | {MIN_CONTROL_DELTA} | {base['delta']} | 校验器整体失效 |",
        f"| 对照组均分上限 | {MAX_CONTROL_AVG_SCORE} | {base['naive_avg']} | 预算规则被放宽 |",
        f"| 对照组硬约束通过率上限 | {MAX_CONTROL_HARD_PASS_RATE} | "
        f"{base['naive_hard_pass_rate']} | 校验器整体失效 |",
        "| 对照组规则覆盖下限 | 见下表 | — | 单条规则被删 |",
        "",
        "| 规则码 | 覆盖下限 | 基准实测命中 |",
        "| --- | ---: | ---: |",
    ]
    for code, floor in CONTROL_RULE_MIN_COUNTS.items():
        lines.append(f"| `{code}` | {floor} | {base['rule_counts'][code]} |")
    lines += [
        "",
        "下限约为实测次数的一半：新增用例不会让门槛漂移，"
        "但删掉一条规则必然让命中次数归零。",
        "",
        "## 回滚验证",
        "",
        f"每次变异退出后都重跑基线并逐字段比对，共 {lab['restored_checks']} 次，"
        f"结论：**{'全部与基线一致' if lab['restored'] else '存在未复原的变异'}**。"
        f"最终 good 均分 {lab['restored_avg']}、硬约束通过率 "
        f"{lab['restored_hard_pass_rate']:.0%}、失败用例 {lab['restored_cases_failed']}。",
        "",
        "这也顺带在 [eval-report.md](eval-report.md) 里留下了一个可复核的"
        "「改动 → 分数下降 → 门禁拦截 → 回滚复原」实例，而不是一句口头承诺。",
        "",
        "## 边界",
        "",
        "变异体是人为构造的，覆盖不了所有可能的实现缺陷；它证明的是"
        "**这八类回归一定会被发现**，不是「门禁万无一失」。"
        "另外实验只使用离线 provider，真实模型接入后应把新的失败模式补成新的变异体。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.mutation_lab --write",
        "python -m evals.mutation_lab --check      # CI：报告过期或出现存活变异即失败",
        "```",
        "",
        "`docs/mutation-report.md` 与 `evals/mutation.json` 均由代码生成，请勿手工编辑。",
        "",
    ]
    return "\n".join(lines)


def build_snapshot(lab: dict[str, Any]) -> str:
    return json.dumps(lab, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def lab_problems(lab: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if lab["baseline_gate_problems"]:
        problems.append(
            f"baseline gate is already failing: {lab['baseline_gate_problems']}"
        )
    survivors = [row["key"] for row in lab["mutants"] if not row["caught"]]
    if survivors:
        problems.append(f"{len(survivors)} mutation(s) survived the gate: {survivors}")
    if not lab["restored"]:
        problems.append("at least one mutation did not roll back to the baseline metrics")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan gate-sensitivity lab")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true", help="CI: gate + stale-artifact check")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--cases-dir", type=Path)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases_dir)
    lab = run_lab(cases)
    document = build_document(lab)
    snapshot = build_snapshot(lab)
    problems = lab_problems(lab)

    if args.check:
        for path, expected in ((args.report_path, document), (args.out, snapshot)):
            if not path.exists():
                problems.append(f"{path} does not exist")
            elif path.read_text(encoding="utf-8") != expected:
                problems.append(f"{path} is stale; run python -m evals.mutation_lab --write")
        if problems:
            print("MUTATION GATE FAILED:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(
            f"mutation gate: OK ({lab['caught']}/{lab['mutant_count']} mutants caught, "
            f"{lab['mutant_count'] - lab['caught_by_legacy_gate']} would have slipped "
            "past the legacy gate)"
        )
        return 0

    if args.write:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(document, encoding="utf-8")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(snapshot, encoding="utf-8")
        print(f"wrote {args.report_path} and {args.out}")

    print(
        f"cases: {lab['case_count']}  mutants: {lab['mutant_count']}  "
        f"caught: {lab['caught']}  survivors: {lab['survivors']}  "
        f"legacy caught: {lab['caught_by_legacy_gate']}  restored: {lab['restored']}"
    )
    for row in lab["mutants"]:
        print(f"  {row['key']:<34} good={row['good_avg']:.4f} "
              f"naive={row['naive_avg']:.4f} caught={row['caught']} "
              f"signals={len(row['signals'])}")
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
