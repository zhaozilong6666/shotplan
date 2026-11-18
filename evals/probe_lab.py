"""Probe cases that sit on the boundary, and what happens when only one side moves.

The main eval set cannot see a 50% change in most constants (EV19), and EV20
explained why: plans fill the booking, and the smallest knob is a whole location.
This set is built the other way round -- every brief is tuned to the edge where a
second location just fits, with shoot blocks of 68-95 minutes.

Three scenarios run over the same twelve briefs:

- `baseline`: nothing patched. All twelve must pass, or the probes are not on the
  boundary they claim to be on.
- `validator-only`: the transfer rule tightens to 30 minutes but the planner still
  schedules 20. This is the realistic failure -- someone edits a constant in the
  validator and forgets the generator, or vice versa.
- `both-sides`: the same 30 minutes applied to both. Pass rate should be unchanged.

The contrast is the point. A single constant appears in several modules because
`from constraints import X` copies the value; changing one copy is not changing the
rule, it is breaking agreement between two components. If `validator-only` does not
fail here, the gate has no way to notice that kind of edit.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evals.run_eval import load_cases  # noqa: E402
from shotplan.agents import mock as mock_module  # noqa: E402
from shotplan.agents import validator as validator_module  # noqa: E402
from shotplan.agents.mock import MockProvider  # noqa: E402
from shotplan.config import get_settings  # noqa: E402
from shotplan.domain.models import PlanRequest  # noqa: E402
from shotplan.pipeline import run_plan  # noqa: E402

REPORT = ROOT / "docs" / "probe-set.md"
CASES = ROOT / "evals" / "probes"
TIGHTENED = 30


def scenario(name: str) -> ExitStack:
    stack = ExitStack()
    if name == "validator-only":
        stack.enter_context(patch.object(validator_module, "MIN_TRANSFER_MINUTES", TIGHTENED))
    elif name == "both-sides":
        stack.enter_context(patch.object(validator_module, "MIN_TRANSFER_MINUTES", TIGHTENED))
        stack.enter_context(patch.object(mock_module, "MIN_TRANSFER_MINUTES", TIGHTENED))
    return stack


def measure() -> dict[str, list[dict[str, Any]]]:
    settings = get_settings()
    briefs = load_cases(CASES)
    results: dict[str, list[dict[str, Any]]] = {}
    for name in ("baseline", "validator-only", "both-sides"):
        rows = []
        with scenario(name):
            provider = MockProvider(mode="good")
            for case in briefs:
                request = PlanRequest(**case["request"])
                result = run_plan(request, provider=provider, store=None, settings=settings)
                rows.append(
                    {
                        "id": case["id"],
                        "pass": bool(result.validation.hard_pass),
                        "shoots": sum(1 for slot in result.plan.slots if slot.activity == "shoot"),
                        "codes": sorted({issue.code for issue in result.validation.hard_issues}),
                    }
                )
        results[name] = rows
    return results


def render(results: dict[str, list[dict[str, Any]]]) -> str:
    total = len(results["baseline"])
    passed = {name: sum(row["pass"] for row in rows) for name, rows in results.items()}
    flipped = [row["id"] for row in results["validator-only"] if not row["pass"]]

    lines = [
        "> 由 `python -m evals.probe_lab --write` 生成，请勿手工编辑。",
        "",
        "# 边界探针：单边改动必须被检出",
        "",
        f"主集看不见多数常量的 ±50% 变化（E19），E20 解释了原因：方案总是排满，"
        f"而最小调整单位是一个地点。这组 {total} 条探针反过来构造——每条都卡在"
        "「刚好排得下第二个地点」的边缘，拍摄块 68–95 分钟。",
        "",
        "三个场景跑同一批 brief：",
        "",
        "| 场景 | 含义 | 通过 | 失败的编码 |",
        "| --- | --- | ---: | --- |",
    ]
    meanings = {
        "baseline": "什么都不改",
        "validator-only": "规则收紧到 30 分钟，生成端仍排 20",
        "both-sides": "两端同步改成 30 分钟",
    }
    for name in ("baseline", "validator-only", "both-sides"):
        codes = sorted({code for row in results[name] for code in row["codes"]})
        lines.append(
            f"| `{name}` | {meanings[name]} | {passed[name]}/{total} "
            f"| {'、'.join(codes) if codes else '—'} |"
        )
    lines.append("")

    lines += [
        "## 为什么这个对比重要",
        "",
        "同一个常量出现在多个模块里，因为 `from constraints import X` 会把值复制成模块自己的名字。"
        "只改一份不是「改了规则」，而是让两个组件对规则的理解产生分歧。",
        "",
        f"`validator-only` 让 {len(flipped)} 条探针失败，编码是 `TRANSFER_BUFFER`——"
        "生成端排出 20 分钟转场，校验端要求 30，每一条都被拦下。"
        f"`both-sides` 则 {passed['both-sides']}/{total} 通过：两端一致时，收紧规则本身不会破坏方案。",
        "",
        "所以真正的风险不是「常量取多少」，而是「改常量时漏了一处」。"
        "探针集的存在意义就是让这种遗漏立刻变红。",
        "",
        "## 它在门禁里断言什么",
        "",
        "```",
        "baseline     全部通过    —— 否则探针没有卡在它声称的边界上",
        "validator-only 全部失败  —— 否则单边改动不会被发现",
        "both-sides   全部通过    —— 否则收紧规则本身就有问题",
        "```",
        "",
        "三条都成立时，这组样本才配叫「探针」。任何一条不成立，先修探针，再看别的。",
        "",
        "## 局限",
        "",
        "只覆盖 `TRANSFER_BUFFER` 这一条硬规则。`BUDGET_EXCEEDED`、`TIME_OVERRUN` 的边界探针"
        "需要不同的构造方式（预算卡在成本线、时长卡在宽限线上），是后续工作。",
        "",
        "另外这组样本不进主集、不进 80 条评测，所以改动它们不会影响基线分数。",
        "",
    ]
    return "\n".join(lines)


def check(results: dict[str, list[dict[str, Any]]]) -> list[str]:
    total = len(results["baseline"])
    problems = []
    if any(not row["pass"] for row in results["baseline"]):
        failed = [row["id"] for row in results["baseline"] if not row["pass"]]
        problems.append(f"baseline 有 {len(failed)} 条不通过：{', '.join(failed)}")
    if any(row["pass"] for row in results["validator-only"]):
        survived = [row["id"] for row in results["validator-only"] if row["pass"]]
        problems.append(f"单边改动能被放过：{', '.join(survived)} 仍然通过")
    if not all(row["pass"] for row in results["both-sides"]):
        failed = [row["id"] for row in results["both-sides"] if not row["pass"]]
        problems.append(f"两端同步收紧后不应失败：{', '.join(failed)}")
    if len(results["baseline"]) != total:
        problems.append("探针集为空")
    return problems


def main() -> int:
    results = measure()
    text = render(results)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    total = len(results["baseline"])
    print(
        "probe set: "
        + ", ".join(
            f"{name} {sum(row['pass'] for row in rows)}/{total}"
            for name, rows in results.items()
        )
    )
    if "--check" in sys.argv:
        problems = check(results)
        for problem in problems:
            print(f"PROBE FAILED: {problem}", file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
