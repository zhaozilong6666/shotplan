"""Offline repair-loop experiment: does feeding the validation report back help?

Three deliberately constructed providers are run over the SAME 30 satisfiable
briefs, with and without a repair round:

* ``naive``       - ignores the repair hint, so repair cannot help (control).
* ``repairable``  - first draft is broken, but any draft produced with a repair
  hint is rule-compliant. Models the behaviour we HOPE a real LLM shows.
* ``regressing``  - first draft has one hard issue, the "repaired" draft is much
  worse. Used to prove the monotonic guard refuses a bad repair.

Every accept/reject decision is read from the run trace (the `accepted` detail
on `repair_*` events), never inferred. This is what turns "we have a repair
loop" into a measurable claim. See docs/repair-experiment.md.

Usage:
    python -m evals.repair_lab --write-report
    python -m evals.repair_lab                 # print summary only
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from math import fsum
from pathlib import Path
from typing import Any

from evals import OUT_DIR
from evals.run_eval import load_cases
from shotplan.agents import MockProvider
from shotplan.agents.base import GenerationResult
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "repair-experiment.md"


class RepairableProvider:
    """First draft is broken; the repair draft obeys every rule."""

    name = "repairable"
    prompt_version = "repair-lab-v1"

    def __init__(self) -> None:
        self._naive = MockProvider("naive")
        self._good = MockProvider("good")

    def generate(self, request, context, repair_hint=None) -> GenerationResult:
        if repair_hint:
            return self._good.generate(request, context, repair_hint)
        return self._naive.generate(request, context, None)


class RegressingProvider:
    """First draft has one hard issue; the "repair" is far worse."""

    name = "regressing"
    prompt_version = "repair-lab-v1"

    def __init__(self) -> None:
        self._good = MockProvider("good")
        self._naive = MockProvider("naive")

    def generate(self, request, context, repair_hint=None) -> GenerationResult:
        if repair_hint:
            return self._naive.generate(request, context, repair_hint)
        draft = self._good.generate(request, context, None)
        payload = dict(draft.payload)
        # A single, precisely located defect: the declared total is inflated.
        payload["total_cost_cny"] = max(1, request.budget_cny * 2)
        return GenerationResult(
            payload=payload,
            tokens_in=draft.tokens_in,
            tokens_out=draft.tokens_out,
            cost_cny=draft.cost_cny,
            duration_ms=draft.duration_ms,
            model=draft.model,
            metadata={**draft.metadata, "lab": "one-hard-issue"},
        )


@dataclass
class ArmResult:
    arm: str
    provider: str
    max_repairs: int
    cases: int = 0
    first_pass: int = 0
    after_pass: int = 0
    repairable_cases: int = 0
    repaired_successfully: int = 0
    repair_attempts: int = 0
    accepted: int = 0
    rejected: int = 0
    avg_score_before: float = 0.0
    avg_score_after: float = 0.0
    details: list[dict[str, Any]] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("details")
        data["first_pass_rate"] = round(self.first_pass / self.cases, 4) if self.cases else 0.0
        data["after_pass_rate"] = round(self.after_pass / self.cases, 4) if self.cases else 0.0
        data["one_shot_repair_rate"] = (
            round(self.repaired_successfully / self.repairable_cases, 4)
            if self.repairable_cases else 0.0
        )
        return data


def _satisfiable_cases() -> list[dict[str, Any]]:
    return [c for c in load_cases() if not c.get("expect_infeasible")]


def _run_case(case: dict[str, Any], provider: Any, max_repairs: int) -> dict[str, Any]:
    result = run_plan(
        PlanRequest.from_dict(case["request"]), provider=provider, max_repairs=max_repairs
    )
    repairs = [e for e in result.trace.events if e.name.startswith("repair_")]
    decisions = [e.detail.get("accepted") for e in repairs if "accepted" in e.detail]
    return {
        "case_id": case["id"],
        "hard_pass": result.validation.hard_pass,
        "score": round(result.validation.score, 4),
        "repair_count": result.trace.repair_count,
        "accepted": sum(1 for d in decisions if d is True),
        "rejected": sum(1 for d in decisions if d is False),
    }


def run_arm(
    cases: list[dict[str, Any]], provider_name: str, max_repairs: int
) -> ArmResult:
    arm = ArmResult(
        arm=f"{provider_name}/repairs={max_repairs}",
        provider=provider_name,
        max_repairs=max_repairs,
    )
    before_scores: list[float] = []
    after_scores: list[float] = []
    for case in cases:
        def make_provider() -> Any:
            if provider_name == "naive":
                return MockProvider("naive")
            if provider_name == "repairable":
                return RepairableProvider()
            if provider_name == "regressing":
                return RegressingProvider()
            raise ValueError(f"unknown arm: {provider_name}")

        baseline = _run_case(case, make_provider(), 0)
        current = (
            baseline if max_repairs == 0
            else _run_case(case, make_provider(), max_repairs)
        )
        before_scores.append(baseline["score"])
        after_scores.append(current["score"])
        arm.cases += 1
        arm.first_pass += int(baseline["hard_pass"])
        arm.after_pass += int(current["hard_pass"])
        if not baseline["hard_pass"]:
            arm.repairable_cases += 1
            if current["hard_pass"]:
                arm.repaired_successfully += 1
        arm.repair_attempts += current["repair_count"]
        arm.accepted += current["accepted"]
        arm.rejected += current["rejected"]
        arm.details.append({"case_id": case["id"], "baseline": baseline, "current": current})
    arm.avg_score_before = round(fsum(before_scores) / len(before_scores), 4)
    arm.avg_score_after = round(fsum(after_scores) / len(after_scores), 4)
    return arm


def check_arms(
    arms: list[ArmResult], min_one_shot_repair_rate: float = 0.90
) -> list[str]:
    """CI gate: the repair loop must work, and must refuse worse drafts."""
    problems: list[str] = []
    by_arm = {a.arm: a.metrics() for a in arms}
    repairable = by_arm.get("repairable/repairs=1", {})
    rate = repairable.get("one_shot_repair_rate", 0.0)
    if rate < min_one_shot_repair_rate:
        problems.append(
            f"one-shot repair rate {rate} is below {min_one_shot_repair_rate}: "
            "the repair hint is not being used"
        )
    for arm_name in ("regressing/repairs=1", "naive/repairs=1"):
        accepted = by_arm.get(arm_name, {}).get("accepted", 0)
        if accepted:
            problems.append(
                f"{arm_name} accepted {accepted} non-improving draft(s); "
                "the monotonic guard is broken"
            )
    return problems


def build_report(arms: list[ArmResult], case_count: int) -> str:
    rows = []
    for arm in arms:
        m = arm.metrics()
        rows.append(
            f"| `{m['arm']}` | {m['first_pass_rate']:.0%} | {m['after_pass_rate']:.0%} | "
            f"{m['one_shot_repair_rate']:.0%} | {m['repair_attempts']} | "
            f"{m['accepted']} / {m['rejected']} | "
            f"{m['avg_score_before']} → {m['avg_score_after']} |"
        )
    by_arm = {a.arm: a for a in arms}
    naive = by_arm["naive/repairs=1"].metrics()
    repairable = by_arm["repairable/repairs=1"].metrics()
    regressing = by_arm["regressing/repairs=1"].metrics()
    return "\n".join([
        "# 修复循环实验报告",
        "",
        f"同一批 **{case_count} 个可满足 brief**，三种刻意构造的 provider，"
        "分别跑 `max_repairs=0`（首轮）与 `max_repairs=1`（允许修复一轮）。",
        "",
        "## 方法",
        "",
        "- `naive`：忽略修复提示，作为「修复无效」的对照组。",
        "- `repairable`：首轮坏方案，带修复提示时产出合规方案，模拟我们期待真实模型具备的行为。",
        "- `regressing`：首轮只有一个硬问题，「修复」后变得更糟，用于检验单调保护。",
        "- 每次接受/拒绝都读 trace 里 `repair_*` 事件的 `accepted` 字段，不靠推断。",
        "",
        "## 结果",
        "",
        "| 实验组 | 首轮通过率 | 修复后通过率 | 一次修复成功率 | 修复轮次 | 接受/拒绝 | 均分变化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        *rows,
        "",
        "## 结论",
        "",
        f"1. **修复提示确实能起作用**：`repairable` 首轮 {repairable['first_pass_rate']:.0%} → "
        f"修复后 {repairable['after_pass_rate']:.0%}，一次修复成功率 "
        f"{repairable['one_shot_repair_rate']:.0%}。",
        f"2. **修复不是免费的**：`naive` 忽略提示，{naive['rejected']} 次修复候选被拒绝，"
        "通过率没有提升。说明修复效果取决于 provider 是否真正使用校验报告。",
        f"3. **单调保护生效**：`regressing` 的 {regressing['rejected']} 次「修复」全部被拒绝，"
        f"均分保持 {regressing['avg_score_before']} → {regressing['avg_score_after']}，"
        "没有把方案改坏。",
        "4. 因此「有修复循环」不等于「质量会变好」：必须有校验器判定、单调保护，"
        "以及像本实验这样量化的成功率指标。",
        "",
        "## 边界",
        "",
        "三个 provider 都是离线确定性构造，用来验证**机制**，不是真实模型能力评估。"
        "接入真实模型后应重跑本实验，把真实的一次修复成功率写进这里，"
        "并把典型失败加入 [failure-gallery.md](failure-gallery.md)。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.repair_lab --write-report",
        "```",
        "",
        "报告与 `evals/out/repair-experiment.json` 均由代码生成，请勿手工编辑。",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan repair-loop experiment")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "repair-experiment.json")
    parser.add_argument("--check", action="store_true", help="CI gate on repair metrics")
    parser.add_argument("--min-one-shot-repair-rate", type=float, default=0.90)
    args = parser.parse_args(argv)

    cases = _satisfiable_cases()
    arms = [
        run_arm(cases, name, repairs)
        for name in ("naive", "repairable", "regressing")
        for repairs in (0, 1)
    ]
    payload = {
        "case_count": len(cases),
        "arms": {a.arm: a.metrics() for a in arms},
        "details": {a.arm: a.details for a in arms},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(build_report(arms, len(cases)), encoding="utf-8")

    print(f"cases: {len(cases)}")
    for arm in arms:
        m = arm.metrics()
        print(f"  {m['arm']:<24} first={m['first_pass_rate']:<6} after={m['after_pass_rate']:<6} "
              f"one-shot={m['one_shot_repair_rate']:<6} accepted/rejected={m['accepted']}/{m['rejected']}")
    problems = check_arms(arms, args.min_one_shot_repair_rate) if args.check else []
    if problems:
        print("REPAIR GATE FAILED:")
        for problem in problems:
            print(f"  - {problem}")
    elif args.check:
        print("repair gate: OK")
    if args.write_report:
        print(f"report: {args.report_path}")
    print(f"raw: {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
