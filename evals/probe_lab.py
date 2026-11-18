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
from shotplan.config import get_settings  # noqa: E402
from shotplan.domain.models import PlanRequest  # noqa: E402
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
)
SCENARIOS = ("baseline", "one-sided", "both-sides")


def run_group(group: Group) -> dict[str, list[dict[str, Any]]]:
    settings = get_settings()
    path = PROBES / f"{group.key}.jsonl"
    briefs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
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


def render(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> str:
    lines = [
        "> 由 `python -m evals.probe_lab --write` 生成，请勿手工编辑。",
        "",
        "# 边界探针：单边改动必须被检出",
        "",
        "主集看不见多数常量的 ±50% 变化（E19），E20 解释了原因：方案总是排满，"
        "而最小调整单位是一个地点。这两组探针反过来构造——每条 brief 都卡在**某一条规则**的边缘。",
        "",
        "每个场景跑同一批 brief：`baseline` 什么都不改；`one-sided` 只在校验端收紧规则，"
        "生成端不变；`both-sides` 在所有绑定该常量的模块里同步收紧。",
        "",
    ]
    for group in GROUPS:
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
        "`TIME_OVERRUN` 还没有探针：planner 不会主动超时，要构造这条边界得注入一个超长的方案，"
        "方式与这两组不同（不是收紧规则，而是伪造生成结果）。留作后续。",
        "",
        "探针集不进主集、不进 80 条评测，改动它们不影响基线分数。",
        "",
    ]
    return "\n".join(lines)


def check(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> list[str]:
    problems = []
    for group in GROUPS:
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
    both = all_results["transfer"]["both-sides"]
    broken = [row["id"] for row in both if not row["pass"]]
    if broken:
        problems.append(f"transfer 两端同步后仍失败：{', '.join(broken)}")
    return problems


def main() -> int:
    all_results = {group.key: run_group(group) for group in GROUPS}
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
    if "--check" in sys.argv:
        problems = check(all_results)
        for problem in problems:
            print(f"PROBE FAILED: {problem}", file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
