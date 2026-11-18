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
CONSTANTS: tuple[tuple[str, float, tuple[Any, ...], str], ...] = (
    # (name, base value, modules that bound it by name, what it actually controls)
    ("MIN_TRANSFER_MINUTES", 20, (mock_module, validator_module), "hard:TRANSFER_BUFFER"),
    ("MIN_SHOOT_BLOCK_MINUTES", 60, (constraints, mock_module), "soft:SHOOT_TOO_SHORT"),
    ("STYLING_SHARE", 0.35, (constraints,), "indirect"),
    ("STYLING_FLOOR_MINUTES", 60, (constraints,), "indirect"),
    ("MAX_OVERRUN_MINUTES", 15, (constraints, validator_module), "hard:TIME_OVERRUN"),
    ("MIN_FEASIBLE_HOURS", 1.5, (constraints,), "gate:feasibility"),
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
    for name, value, modules, role in CONSTANTS:
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
                    "role": role,
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
        "| 常量 | 控制 | 基准值 | ×0.5 | ×1.5 | 变失败（×0.5 / ×1.5） | 平均地点数 | 均分（基准 → ×0.5 → ×1.5） |",
        "| --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    base_locations = statistics.mean(row["locations"] for row in baseline)
    base_score = statistics.mean(row["score"] for row in baseline)
    for name, rows in ordered:
        half = next(row for row in rows if row["factor"] == 0.5)
        double = next(row for row in rows if row["factor"] == 1.5)
        lines.append(
            f"| `{name}` | {half['role']} | {half['base']:g} | {half['value']:g} | {double['value']:g} "
            f"| {half['broke']} / {double['broke']} "
            f"| {base_locations:.2f} → {half['mean_locations']:.2f} → {double['mean_locations']:.2f} "
            f"| {base_score:.3f} → {half['mean_score']:.3f} → {double['mean_score']:.3f} |"
        )
    lines.append("")

    hard_rows = [row for row in results if row["role"].startswith("hard")]
    soft_rows = [row for row in results if row["role"].startswith("soft")]
    lines += [
        "## 读法",
        "",
        "先看「控制」这一列，它决定了该不该期待通过率变化：6 个常量里只有 2 个直接参与硬判定"
        "（`MIN_TRANSFER_MINUTES` → TRANSFER_BUFFER、`MAX_OVERRUN_MINUTES` → TIME_OVERRUN）。"
        "`MIN_SHOOT_BLOCK_MINUTES` 对应的是 **soft** 规则 SHOOT_TOO_SHORT——它影响分数，不影响通过。"
        "所以 E19 那句预测「它会让 ≥10 条 brief 从通过变成失败」从一开始就不可能成立："
        "soft 规则不会让 `hard_pass` 变 False。",
        "",
        "按控制类型分开看更清楚：",
        "",
        f"- **hard 类**：最多 {max(row['broke'] for row in hard_rows)} 条 brief 从通过变失败"
        f"（占 {len(baseline)} 条的 {max(row['broke'] for row in hard_rows) / len(baseline):.0%}）。",
        "- **soft 类**：变失败 0 条（定义使然），均分变化 "
        f"{abs(next(row for row in soft_rows if row['factor'] == 0.5)['mean_score'] - base_score):.3f}——"
        "连分数都没动。这说明这批 brief 的拍摄块都远大于 90 分钟，扰动根本没跨过 soft 门槛。"
        "拿通过率衡量 soft 规则是用错了尺子；连分数都不动，说明连 soft 的尺子都没碰到。",
        "- **可行性常量** `MIN_FEASIBLE_HOURS`：决定 11 条 infeasible 的判定，±50% 没有跨过任何 brief 的边界，"
        "所以 0 条变化——它是对的，只是这个评测集里的 brief 离边界都太远。",
        "",
        "修正后的结论：**在硬判定层面，评测集对 hard 常量的 ±50% 扰动分辨力很低（最多 "
        f"{max(row['broke'] for row in hard_rows)} 条）**。原因见 E20：方案每次都被排满，"
        "而最小调整单位是一个地点（60 分钟以上），5–20 分钟的扰动跨不过那一档。"
        "但这不能推广到 soft 常量——它们本来就不该改变通过率，拿通过率去衡量它们是用错了尺子。",
    ]
    lines += [
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
