"""Probe cases that sit on a rule's boundary, and what a one-sided edit does to them.

The main eval set cannot see a 50% change in most constants (EV19), and EV20
explained why: plans fill the booking and the smallest knob is a whole location.
These sets are built the other way round -- every brief is tuned to the edge of one
specific rule.

Two groups:

- `transfer`: twelve briefs where a second location just fits, shoot blocks 68-95
  minutes, transfer exactly at the floor.
- `budget`: four briefs whose budget equals the plan's exact cost, so a single yuan
  of tolerance decides the outcome.

Each group runs three scenarios over the same briefs:

- `baseline`: nothing patched. Every brief must pass, or it is not on the boundary
  it claims to be on.
- `one-sided`: the rule tightened in the validator only, planner unchanged. This is
  the realistic failure -- someone edits one copy of a constant.
- `both-sides`: the same tightening applied everywhere that binds the name. Pass
  rate should be unchanged.

`budget` has only one side: the planner never reads the tolerance, it just spends
what it spends. That asymmetry is why its `one-sided` and `both-sides` rows are
identical, and it is worth seeing next to `transfer`, where they differ.
"""

from __future__ import annotations

import copy
import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shotplan.agents import mock as mock_module  # noqa: E402
from shotplan.agents import validator as validator_module  # noqa: E402
from shotplan.agents.mock import MockProvider  # noqa: E402
from shotplan.agents.validator import validate  # noqa: E402
from shotplan.config import get_settings  # noqa: E402
from shotplan.domain import constraints as constraints_module  # noqa: E402
from shotplan.domain import feasibility as feasibility_module  # noqa: E402
from shotplan.domain.feasibility import check_feasibility  # noqa: E402
from shotplan.domain.models import PlanRequest, fmt_minutes, to_minutes  # noqa: E402
from shotplan.knowledge.tools import KnowledgeTools  # noqa: E402
from shotplan.pipeline import run_plan  # noqa: E402

REPORT = ROOT / "docs" / "probe-set.md"
PROBES = ROOT / "evals" / "probes"


@dataclass(frozen=True)
class Group:
    key: str
    constant: str
    tightened: float
    modules: tuple[Any, ...]
    note: str
    kind: str = "plan"


GROUPS = (
    Group(
        "transfer", "MIN_TRANSFER_MINUTES", 30,
        (validator_module, mock_module),
        "转场下限收紧到 30 分钟，生成端仍排 20",
    ),
    Group(
        "budget", "BUDGET_TOLERANCE_CNY", -50,
        (validator_module,),
        "预算容差收紧到 −50 元，方案成本刚好等于预算",
    ),
    Group(
        "budget-floor", "MIN_TRANSFER_COST_CNY", 0,
        (feasibility_module,),
        "最低转场成本改成 0 元：预算低于最低线的 brief 会被误判为可行",
        kind="feasibility",
    ),
    Group(
        "hours-floor", "MIN_FEASIBLE_HOURS", 0,
        (feasibility_module,),
        "1.5 小时下限改成 0：排不下的短预约会被误判为可行",
        kind="feasibility",
    ),
)

PLAN_GROUPS = tuple(group for group in GROUPS if group.kind == "plan")
FEASIBILITY_GROUPS = tuple(group for group in GROUPS if group.kind == "feasibility")
SCENARIOS = ("baseline", "one-sided", "both-sides")


def read_group(key: str) -> list[dict[str, Any]]:
    path = PROBES / f"{key}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_time_group() -> dict[str, list[dict[str, Any]]]:
    """The one group that does not patch a constant: it forges the output instead.

    `TIME_OVERRUN` cannot be probed by tightening a rule, because the planner never
    produces an overrun on purpose -- it schedules inside the booking. So the probe
    takes a valid plan and pushes its last block 20 minutes past the limit, then asks
    the validator alone. That is the only way to test whether the check works when
    the generator is not cooperating.
    """
    settings = get_settings()
    rows: dict[str, list[dict[str, Any]]] = {"baseline": [], "inflated": []}
    for case in read_group("time"):
        request = PlanRequest(**case["request"])
        result = run_plan(request, provider=MockProvider(mode="good"), store=None, settings=settings)
        base = validate(result.plan, request)
        stretched = copy.deepcopy(result.plan)
        stretched.slots[-1].end = fmt_minutes(to_minutes(stretched.slots[-1].end) + 20)
        report = validate(stretched, request)
        rows["baseline"].append(
            {
                "id": case["id"],
                "pass": bool(base.hard_pass),
                "codes": sorted({issue.code for issue in base.hard_issues}),
            }
        )
        rows["inflated"].append(
            {
                "id": case["id"],
                "pass": bool(report.hard_pass),
                "codes": sorted({issue.code for issue in report.hard_issues}),
            }
        )
    return rows


def run_shoot_floor_group() -> dict[str, list[dict[str, Any]]]:
    """A soft rule, probed by forging plans just below and at the floor.

    `SHOOT_TOO_SHORT` does not change `hard_pass`, so the plan-level probes cannot
    see it. Take a valid plan, shorten one shoot block to `floor - 1` and to
    `floor`, and assert the issue appears only in the first case. Disabling the
    floor constant must make the below-floor case disappear again.
    """
    settings = get_settings()
    rows: dict[str, list[dict[str, Any]]] = {"baseline": [], "disabled": []}
    for case in read_group("shoot-floor"):
        request = PlanRequest(**case["request"])
        result = run_plan(request, provider=MockProvider(mode="good"), store=None, settings=settings)
        floor = constraints_module.min_shoot_block_minutes(request.hours)
        for scenario in ("baseline", "disabled"):
            with ExitStack() as stack:
                if scenario == "disabled":
                    stack.enter_context(
                        patch.object(constraints_module, "MIN_SHOOT_BLOCK_FLOOR", 0)
                    )
                for label, minutes in (("below", max(1, floor - 1)), ("at-floor", floor)):
                    forged = copy.deepcopy(result.plan)
                    slot = next(s for s in forged.slots if s.activity == "shoot")
                    slot.end = fmt_minutes(to_minutes(slot.start) + minutes)
                    report = validate(forged, request)
                    hit = any(issue.code == "SHOOT_TOO_SHORT" for issue in report.issues)
                    expect = label == "below"
                    rows[scenario].append(
                        {
                            "id": f"{case['id']}-{label}",
                            "pass": hit == expect,
                            "hit": hit,
                            "expect_issue": expect,
                            "score": round(report.score, 4),
                            "floor": floor,
                        }
                    )
    return rows


def run_group(group: Group) -> dict[str, list[dict[str, Any]]]:
    settings = get_settings()
    briefs = read_group(group.key)
    results: dict[str, list[dict[str, Any]]] = {}
    for scenario in SCENARIOS:
        rows = []
        with ExitStack() as stack:
            if scenario != "baseline":
                targets = group.modules[:1] if scenario == "one-sided" else group.modules
                for module in targets:
                    if hasattr(module, group.constant):
                        stack.enter_context(patch.object(module, group.constant, group.tightened))
            provider = MockProvider(mode="good")
            for case in briefs:
                request = PlanRequest(**case["request"])
                result = run_plan(request, provider=provider, store=None, settings=settings)
                rows.append(
                    {
                        "id": case["id"],
                        "pass": bool(result.validation.hard_pass),
                        "codes": sorted({issue.code for issue in result.validation.hard_issues}),
                    }
                )
        results[scenario] = rows
    return results


def run_feasibility_group(group: Group) -> dict[str, list[dict[str, Any]]]:
    """A gate constant: the probe asserts the accept/reject decision, not a score.

    These groups exist because a gate can fail in the dangerous direction without
    changing any plan: lower the floor, and an impossible brief silently becomes
    "feasible". No plan is generated, so `run_group`'s hard-pass model cannot see it.
    """
    tools = KnowledgeTools()
    briefs = read_group(group.key)
    results: dict[str, list[dict[str, Any]]] = {}
    for scenario in SCENARIOS:
        rows = []
        with ExitStack() as stack:
            if scenario != "baseline":
                targets = group.modules[:1] if scenario == "one-sided" else group.modules
                for module in targets:
                    stack.enter_context(patch.object(module, group.constant, group.tightened))
            for case in briefs:
                report = check_feasibility(PlanRequest(**case["request"]), tools)
                rows.append(
                    {
                        "id": case["id"],
                        "pass": report.feasible == case["expect_feasible"],
                        "feasible": report.feasible,
                        "expect_feasible": case["expect_feasible"],
                        "codes": sorted({reason.code for reason in report.reasons}),
                    }
                )
        results[scenario] = rows
    return results


def render(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> str:
    lines = [
        "> 由 `python -m evals.probe_lab --write` 生成，请勿手工编辑。",
        "",
        "# 边界探针：单边改动必须被检出",
        "",
        "主集看不见多数常量的 ±50% 变化（E19），E20 解释了原因：方案总是排满，"
        "而最小调整单位是一个地点。这些探针反过来构造——每条 brief 都卡在**某一条规则或某一道闸门**的边缘。",
        "",
        "`transfer` / `budget` 跑完整流水线：`baseline` 什么都不改，`one-sided` 只收紧校验端，"
        "`both-sides` 在所有绑定该常量的模块里同步收紧。`budget-floor` / `hours-floor` 不生成方案，"
        "只调用可行性判定——把闸门常量改成 0，原本该拒绝的 brief 会被误判为可行。`time` 伪造一份"
        "超时方案，单独交给校验器。",
        "",
    ]
    for group in PLAN_GROUPS:
        results = all_results[group.key]
        total = len(results["baseline"])
        lines += [
            f"## {group.key}（{total} 条）",
            "",
            f"{group.note}。",
            "",
            "| 场景 | 通过 | 失败的编码 |",
            "| --- | ---: | --- |",
        ]
        for scenario in SCENARIOS:
            passed = sum(row["pass"] for row in results[scenario])
            codes = sorted({code for row in results[scenario] for code in row["codes"]})
            lines.append(f"| `{scenario}` | {passed}/{total} | {'、'.join(codes) if codes else '—'} |")
        lines.append("")

    time_results = all_results["time"]
    total = len(time_results["baseline"])
    lines += [
        f"## time（{total} 条）",
        "",
        "这组不收紧规则，而是**伪造生成结果**：把方案的最后一块延长 20 分钟，交给校验器单独判断。"
        "`TIME_OVERRUN` 无法用「调紧常量」来探——planner 从不在预约时长外排期，所以正常路径永远不触发。",
        "",
        "| 场景 | 通过 | 失败的编码 |",
        "| --- | ---: | --- |",
    ]
    for scenario in ("baseline", "inflated"):
        passed = sum(row["pass"] for row in time_results[scenario])
        codes = sorted({code for row in time_results[scenario] for code in row["codes"]})
        lines.append(f"| `{scenario}` | {passed}/{total} | {'、'.join(codes) if codes else '—'} |")
    lines += [
        "",
        "`inflated` 全部变成 `TIME_OVERRUN`，说明这条检查只依赖方案本身，不依赖生成端配合。"
        "这正是校验器该有的性质：**它验证结果，不验证过程**。",
        "",
    ]

    for group in FEASIBILITY_GROUPS:
        results = all_results[group.key]
        total = len(results["baseline"])
        lines += [
            f"## {group.key}（{total} 条）",
            "",
            f"{group.note}。",
            "",
            "| 场景 | 符合预期 | 判定分布 | 误判 |",
            "| --- | ---: | --- | --- |",
        ]
        for scenario in SCENARIOS:
            rows = results[scenario]
            passed = sum(row["pass"] for row in rows)
            feasible = sum(row["feasible"] for row in rows)
            wrong = [row["id"] for row in rows if not row["pass"]]
            lines.append(
                f"| `{scenario}` | {passed}/{total} | 可行 {feasible} / 不可行 {total - feasible} "
                f"| {'、'.join(wrong) if wrong else '—'} |"
            )
        lines.append("")

    lines += [
        "这两组的 `baseline` 是「闸门该拒绝的拒绝、该放行的放行」；`one-sided` 把常量改成 0 之后，"
        "**边界内侧那条 brief 被误判为可行**——不生成任何方案，也不会撞硬规则，只有直接断言"
        "可行性判定才看得见。这正是 E22 里评测集漏掉的那类改动。",
        "",
    ]

    shoot = all_results["shoot-floor"]
    total = len(shoot["baseline"])
    lines += [
        f"## shoot-floor（{total // 2} 条 brief / {total} 个断言）",
        "",
        "`SHOOT_TOO_SHORT` 是软规则，不影响 `hard_pass`，方案级探针看不见它。这里把同一份方案的"
        "拍摄块压到 `floor - 1` 与正好 `floor`：前者应命中，后者不应命中。把 "
        "`MIN_SHOOT_BLOCK_FLOOR` 改成 0 后，1.5 小时那档的门槛从 30 掉到 22，"
        "低于 30 的断言应当失守。",
        "",
        "| 场景 | 符合预期 | 命中 | 最低分 | 失守的断言 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for scenario in ("baseline", "disabled"):
        rows = shoot[scenario]
        passed = sum(row["pass"] for row in rows)
        hits = sum(row["hit"] for row in rows)
        low = min(row["score"] for row in rows)
        wrong = [row["id"] for row in rows if not row["pass"]]
        lines.append(
            f"| `{scenario}` | {passed}/{len(rows)} | {hits}/{len(rows)} | {low:.4f} "
            f"| {'、'.join(wrong) if wrong else '—'} |"
        )
    lines += [
        "",
        f"`baseline` 的 {total // 2} 条 below 全部命中、{total // 2} 条 at-floor 全部不命中；"
        "`disabled` 只剩下门槛仍大于 30 分钟的那几档还在命中——1.5 小时那档被放过。软规则不会让方案失败，"
        "但它的沉默同样是一种回归。",
        "",
    ]

    transfer = all_results["transfer"]
    budget = all_results["budget"]
    lines += [
        "## 为什么这个对比重要",
        "",
        "同一个常量出现在多个模块里，因为 `from constraints import X` 会把值复制成模块自己的名字。"
        "只改一份不是「改了规则」，而是让两个组件对规则的理解产生分歧。",
        "",
        f"`transfer` 的 `one-sided` 让 "
        f"{sum(1 for row in transfer['one-sided'] if not row['pass'])} 条探针失败——"
        "生成端排出 20 分钟转场，校验端要求 30，每一条都被拦下。"
        f"`both-sides` 则 {sum(row['pass'] for row in transfer['both-sides'])}/"
        f"{len(transfer['both-sides'])} 通过：两端一致时，收紧规则本身不破坏方案。",
        "",
        f"`budget` 的两行相同（{sum(row['pass'] for row in budget['one-sided'])}/"
        f"{len(budget['one-sided'])}），因为生成端根本不读容差——它只花钱。"
        "这种「规则只有一侧」的常量，单边改动就是全部改动，不存在不一致。",
        "",
        "**真正的风险不是「常量取多少」，而是「改常量时漏了一处」。**"
        "探针让这种遗漏立刻变红，代价只有十几条 brief 的运行时。",
        "",
        "## 它在门禁里断言什么",
        "",
        "```",
        "每组 baseline       全部通过  —— 否则探针没有卡在它声称的边界上",
        "每组 one-sided      全部失败  —— 否则单边改动不会被发现",
        "transfer both-sides 全部通过  —— 否则收紧规则本身就有问题",
        "```",
        "",
        "三条都成立时，这组样本才配叫「探针」。任何一条不成立，先修探针，再看别的。",
        "",
        "## 局限",
        "",
        "探针覆盖三类边界：常量单边改动（`transfer` / `budget`）、闸门常量失效"
        "（`budget-floor` / `hours-floor`）、伪造超时（`time`）。它不覆盖行级并发、崩溃恢复"
        "与真实模型的输出分布——那三类分别由并发测试、崩溃恢复测试与真实模型实验负责。",
        "",
        "探针集不进主集、不进 80 条评测，改动它们不影响基线分数。",
        "",
    ]
    return "\n".join(lines)


def check(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> list[str]:
    problems = []
    for group in PLAN_GROUPS:
        results = all_results[group.key]
        baseline = results["baseline"]
        if not baseline:
            problems.append(f"{group.key}: 探针集为空")
            continue
        failed = [row["id"] for row in baseline if not row["pass"]]
        if failed:
            problems.append(f"{group.key} baseline 不通过：{', '.join(failed)}")
        survived = [row["id"] for row in results["one-sided"] if row["pass"]]
        if survived:
            problems.append(f"{group.key} 单边改动被放过：{', '.join(survived)}")
    for group in FEASIBILITY_GROUPS:
        results = all_results[group.key]
        baseline = results["baseline"]
        if not baseline:
            problems.append(f"{group.key}: 探针集为空")
            continue
        failed = [row["id"] for row in baseline if not row["pass"]]
        if failed:
            problems.append(f"{group.key} baseline 不通过：{', '.join(failed)}")
        if not any(not row["expect_feasible"] for row in baseline):
            problems.append(f"{group.key} 没有卡在拒绝侧的 brief，闸门失效也看不出来")
        missed = [
            row["id"]
            for row in results["one-sided"]
            if not row["expect_feasible"] and row["pass"]
        ]
        if missed:
            problems.append(f"{group.key} 闸门常量失效后仍被判不可行：{', '.join(missed)}")
        broken = [
            row["id"]
            for row in results["one-sided"]
            if row["expect_feasible"] and not row["pass"]
        ]
        if broken:
            problems.append(f"{group.key} 闸门常量失效后原本可行的 brief 被拒绝：{', '.join(broken)}")
    shoot = all_results["shoot-floor"]
    baseline = shoot["baseline"]
    if not baseline:
        problems.append("shoot-floor: 探针集为空")
    else:
        failed = [row["id"] for row in baseline if not row["pass"]]
        if failed:
            problems.append(f"shoot-floor baseline 不通过：{', '.join(failed)}")
        if not any(row["expect_issue"] for row in baseline):
            problems.append("shoot-floor 没有低于门槛的断言，门槛失效也看不出来")
        missed = [
            row["id"]
            for row in shoot["disabled"]
            if row["expect_issue"] and not row["pass"]
        ]
        if not missed:
            problems.append("shoot-floor 门槛失效后没有任何 below 断言失守")
        broken = [
            row["id"]
            for row in shoot["disabled"]
            if not row["expect_issue"] and not row["pass"]
        ]
        if broken:
            problems.append(f"shoot-floor 门槛失效后 at-floor 断言反而命中：{', '.join(broken)}")
    both = all_results["transfer"]["both-sides"]
    broken = [row["id"] for row in both if not row["pass"]]
    if broken:
        problems.append(f"transfer 两端同步后仍失败：{', '.join(broken)}")
    time_results = all_results["time"]
    if not time_results["baseline"]:
        problems.append("time: 探针集为空")
    else:
        failed = [row["id"] for row in time_results["baseline"] if not row["pass"]]
        if failed:
            problems.append(f"time baseline 不通过：{', '.join(failed)}")
        missed = [row["id"] for row in time_results["inflated"] if row["pass"]]
        if missed:
            problems.append(f"time 注入超时未被检出：{', '.join(missed)}")
    return problems


def main() -> int:
    all_results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for group in GROUPS:
        all_results[group.key] = (
            run_feasibility_group(group) if group.kind == "feasibility" else run_group(group)
        )
    all_results["time"] = run_time_group()
    all_results["shoot-floor"] = run_shoot_floor_group()
    text = render(all_results)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    for group in GROUPS:
        results = all_results[group.key]
        total = len(results["baseline"])
        print(
            f"probe {group.key}: "
            + ", ".join(
                f"{scenario} {sum(row['pass'] for row in results[scenario])}/{total}"
                for scenario in SCENARIOS
            )
        )
    time_total = len(all_results["time"]["baseline"])
    print(
        "probe time: "
        + ", ".join(
            f"{scenario} {sum(row['pass'] for row in all_results['time'][scenario])}/{time_total}"
            for scenario in ("baseline", "inflated")
        )
    )
    shoot = all_results["shoot-floor"]
    shoot_total = len(shoot["baseline"])
    print(
        "probe shoot-floor: "
        + ", ".join(
            f"{scenario} {sum(row['pass'] for row in shoot[scenario])}/{shoot_total}"
            for scenario in ("baseline", "disabled")
        )
    )
    if "--check" in sys.argv:
        problems = check(all_results)
        for problem in problems:
            print(f"PROBE FAILED: {problem}", file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
