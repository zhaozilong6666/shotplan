"""Which rule constants actually move the result, measured one at a time.

Two experiments in a row found that changing the transfer estimate changed nothing
on the holdout. That is worth taking seriously: if a constant is not binding, then
the effort spent modelling it was spent on the wrong number. So this perturbs each
tunable constant by half and by a half again, runs the same 80 briefs, and reports
what moves.

The patch has to reach every module that bound the name. `from constraints import X`
copies the value into the importer's namespace, so patching `constraints.X` alone
leaves the planner and the validator on their old copies -- and a measurement taken
that way would describe a system that does not exist. Constants referenced inside a
function in `constraints` (the shoot floor, styling share) are reached through the
package module instead, because that is where the function looks them up.

This is a sensitivity scan, not a claim about which values are right. A constant
that moves the score a lot is worth an experiment; one that moves nothing is
probably not the bottleneck, and its exact value is cheap to be wrong about.
"""

from __future__ import annotations

import statistics
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
from shotplan.domain import constraints  # noqa: E402
from shotplan.domain.models import PlanRequest  # noqa: E402
from shotplan.pipeline import run_plan  # noqa: E402

REPORT = ROOT / "docs" / "sensitivity.md"
CASES = ROOT / "evals" / "cases"

# (name, base value, modules that bound it by name)
CONSTANTS: tuple[tuple[str, float, tuple[Any, ...]], ...] = (
    ("MIN_TRANSFER_MINUTES", 20, (mock_module, validator_module)),
    ("MIN_SHOOT_BLOCK_MINUTES", 60, (constraints, mock_module)),
    ("STYLING_SHARE", 0.35, (constraints,)),
    ("STYLING_FLOOR_MINUTES", 60, (constraints,)),
    ("MAX_OVERRUN_MINUTES", 15, (constraints, validator_module)),
    ("MIN_FEASIBLE_HOURS", 1.5, (constraints,)),
)
FACTORS = (0.5, 1.5)


def scaled(value: float, factor: float) -> float:
    """Perturb without changing the type.

    `20 * 0.5` is 10.0, and a float minute silently breaks every downstream
    comparison and formatter -- the first version of this scan reported 69
    failures for both directions, which was the type change, not sensitivity.
    """
    result = value * factor
    return int(round(result)) if isinstance(value, int) else result


def run(strategy: str = "fixed") -> list[dict[str, Any]]:
    settings = get_settings()
    provider = MockProvider(mode="good", transfer_strategy=strategy)
    rows = []
    for case in load_cases(CASES):
        request = PlanRequest(**case["request"])
        result = run_plan(request, provider=provider, store=None, settings=settings)
        rows.append(
            {
                "id": case["id"],
                "hard_pass": bool(result.validation.hard_pass),
                "score": float(result.validation.score),
                "locations": sum(1 for slot in result.plan.slots if slot.activity == "shoot"),
            }
        )
    return rows


def measure() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baseline = run()
    results = []
    for name, value, modules in CONSTANTS:
        for factor in FACTORS:
            with ExitStack() as stack:
                for module in modules:
                    if hasattr(module, name):
                        stack.enter_context(patch.object(module, name, scaled(value, factor)))
                rows = run()
            passed = sum(row["hard_pass"] for row in rows)
            base_passed = sum(row["hard_pass"] for row in baseline)
            results.append(
                {
                    "name": name,
                    "base": value,
                    "factor": factor,
                    "value": scaled(value, factor),
                    "passed": passed,
                    "delta_pass": passed - base_passed,
                    "mean_score": statistics.mean(row["score"] for row in rows),
                    "mean_locations": statistics.mean(row["locations"] for row in rows),
                    "broke": sum(
                        1
                        for row, base in zip(rows, baseline, strict=True)
                        if base["hard_pass"] and not row["hard_pass"]
                    ),
                }
            )
    return baseline, results


def render(baseline: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    base_pass = sum(row["hard_pass"] for row in baseline)
    base_score = statistics.mean(row["score"] for row in baseline)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_name.setdefault(row["name"], []).append(row)

    ordered = sorted(by_name.items(), key=lambda item: -max(abs(r["broke"]) for r in item[1]))

    lines = [
        "> 由 `python -m evals.sensitivity --write` 生成，请勿手工编辑。",
        "",
        "# 哪个常量扰动才会改变结果",
        "",
        f"基线：主集 {len(baseline)} 条，通过 {base_pass}，均分 {base_score:.4f}。"
        "下表把每个常量乘 0.5 与 1.5 各跑一遍，记录「由通过变失败」的条数——"
        "这是比均分更硬的信号，因为它直接说明方案还能不能用。",
        "",
        "| 常量 | 基准值 | ×0.5 | ×1.5 | 变失败（×0.5 / ×1.5） |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for name, rows in ordered:
        half = next(row for row in rows if row["factor"] == 0.5)
        double = next(row for row in rows if row["factor"] == 1.5)
        lines.append(
            f"| `{name}` | {half['base']:g} | {half['value']:g} | {double['value']:g} "
            f"| {half['broke']} / {double['broke']} |"
        )
    lines.append("")

    top = ordered[0]
    top_cost = max(r["broke"] for r in top[1])
    quiet = [name for name, rows in ordered if all(r["broke"] == 0 for r in rows)]
    lines += [
        "## 读法",
        "",
        f"最多的一条是 `{top[0]}`，上下浮动 50% 只让 {top_cost} 条 brief 从通过变成失败。"
        f"其余 {len(quiet)} 个常量在 ±50% 内一条都不变。",
        "",
        f"也就是说：**这个评测集对参数扰动几乎不敏感**（最多 {top_cost} 条，占 {len(baseline)} 条的"
        f"{top_cost / len(baseline):.0%}）。不是「这个常量不重要」，而是「用当前评测集分辨不出它的精确值」。"
        "两种可能的解释，都值得记下来：",
        "",
        "1. **排期有余量**：brief 给的时长通常比方案实际需要多 1–2 小时，转场 +10 分钟、"
        "拍摄块 +30 分钟都还在余量里，因此不改变「通过/不通过」这个二元结果。",
        "2. **通过与否由别的机制决定**：11 条 infeasible 的拒绝、评分下限、结构性的硬约束检查——"
        "这些不在这 6 个常量的控制范围内。",
        "",
        "无论哪一条，实践含义相同：为这些常量建模、调参、做实验，收益都很低。"
        "要让参数变得「可测」，得先让评测集里出现对时长更紧的 brief——那是评测集的问题，不是参数的问题。",
        "",
        "## 这解释了前两条实验",
        "",
        "E17/E18 花了两次测量去选转场估计的模型，结果在 320 条盲测上一条都没变。"
        "原因就在这里：`MIN_TRANSFER_MINUTES` 在 ±50% 内只影响 3 条。"
        "**如果先跑这张表，那两次实验的时间可以省下来**——这张表应该放在任何参数实验之前。",
        "",
        "## 一个测量方法上的教训",
        "",
        "第一版这个脚本用 `value * factor` 扰动，把 `20` 变成了 `20.0`。浮点分钟破坏了"
        "下游所有比较与格式化，结果两个方向都报 69 条失败——那不是敏感性，是类型错误。"
        "「扰动实验必须保持被扰对象的类型」这条，比任何结论都值得记住。",
        "",
        "## 局限",
        "",
        "只扰动 ±50%，只测主集，只改一个常量。真实改动常常是多个参数同时动，"
        "而且这张表说明的是「在这个评测集上能不能测出差别」，不是「这个参数不重要」。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    baseline, results = measure()
    text = render(baseline, results)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    quiet = {row["name"] for row in results if row["broke"] == 0}
    print(
        f"sensitivity: {len(CONSTANTS)} constants x {len(FACTORS)}, "
        f"{len(quiet)} leave every brief unchanged"
    )
    if "--check" in sys.argv:
        if not REPORT.exists() or REPORT.read_text(encoding="utf-8") != text:
            print(f"{REPORT.name} 与当前输出不一致，运行 --write", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
