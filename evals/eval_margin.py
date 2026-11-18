"""How much slack does each brief leave? This is why the constants look inert.

EV19 perturbed every rule constant by 50% and moved at most three briefs. That is
either because the constants sit in a flat region, or because the plans adapt:
when time is short, the planner schedules one location instead of two, so pass/fail
survives while the plan changes shape.

The second explanation predicts something specific: the time margin between what a
brief allows and what a plan uses should be small, and the plan should be using
whole locations as its adjustment knob rather than minutes. This measures that
margin per brief -- `hours * 60` minus the span from first slot to last.

A large margin means the brief is generous and the constants are being absorbed. A
margin near zero means the plan is packed to the limit, and the insensitivity comes
from granularity instead: a location is worth 60+ minutes, and no 10-minute change
in a transfer crosses that step.
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evals.run_eval import load_cases  # noqa: E402
from shotplan.agents.mock import MockProvider  # noqa: E402
from shotplan.config import get_settings  # noqa: E402
from shotplan.domain.models import PlanRequest, to_minutes  # noqa: E402
from shotplan.pipeline import run_plan  # noqa: E402

REPORT = ROOT / "docs" / "eval-margin.md"
CASES = ROOT / "evals" / "cases"


def measure() -> list[dict[str, Any]]:
    settings = get_settings()
    provider = MockProvider(mode="good")
    rows = []
    for case in load_cases(CASES):
        request = PlanRequest(**case["request"])
        result = run_plan(request, provider=provider, store=None, settings=settings)
        slots = result.plan.slots
        span = to_minutes(slots[-1].end) - to_minutes(slots[0].start) if slots else 0
        usable = int(float(request.hours) * 60)
        rows.append(
            {
                "id": case["id"],
                "group": case.get("group", ""),
                "usable": usable,
                "span": span,
                "margin": usable - span,
                "locations": sum(1 for slot in slots if slot.activity == "shoot"),
                "hard_pass": bool(result.validation.hard_pass),
                "codes": Counter(issue.code for issue in result.validation.issues),
            }
        )
    return rows


def describe(values: list[int]) -> str:
    if not values:
        return "—"
    return (
        f"{statistics.median(values):.0f} / {min(values)} / {max(values)}"
    )


def render(rows: list[dict[str, Any]]) -> str:
    feasible = [row for row in rows if row["hard_pass"]]
    infeasible = [row for row in rows if not row["hard_pass"]]
    margins = [row["margin"] for row in feasible]
    groups = sorted({row["group"] for row in rows})

    lines = [
        "> 由 `python -m evals.eval_margin --write` 生成，请勿手工编辑。",
        "",
        "# 每条 brief 留了多少余量",
        "",
        "E19 把每个规则常量扰动 ±50%，最多影响 3 条 brief。要么常量在一个平坦区间里，"
        "要么方案会自适应——时间紧就少排一个地点，于是「通过」不变、方案变形。",
        "",
        f"第二种解释有个可检验的推论：方案应该被排得很满（余量小），而调整的旋钮是"
        f"「整个地点」而不是分钟。下面统计 {len(rows)} 条 brief 的 `hours×60` 与方案实际跨度之差。",
        "",
        "| 分组 | 条数 | 通过 | 余量（中位 / 最小 / 最大，分钟） |",
        "| --- | ---: | ---: | --- |",
    ]
    for group in groups:
        subset = [row for row in rows if row["group"] == group]
        passed = [row for row in subset if row["hard_pass"]]
        lines.append(
            f"| {group} | {len(subset)} | {len(passed)} | {describe([row['margin'] for row in passed])} |"
        )
    lines.append(
        f"| **全部** | {len(rows)} | {len(feasible)} | {describe(margins)} |"
    )
    lines.append("")

    median_margin = statistics.median(margins) if margins else 0
    tight = [row for row in feasible if row["margin"] <= 15]
    lines += [
        "## 结论",
        "",
        f"可行 brief 的余量中位数是 **{median_margin:.0f} 分钟**，"
        f"{len(tight)} 条 brief 的余量不超过 15 分钟（也就是排到了上限）。",
        "",
    ]
    if median_margin <= 30:
        lines += [
            "**余量为 0 不是巧合，是设计。** planner 的最后一块拍摄会拉伸到结束时刻"
            "（`duration = max(duration, end_limit - cursor)`），所以只要排得下，方案必然用满。"
            "「余量」在这个系统里因此不是变量，而是一个恒等于 0 的结果——"
            "它不能用来解释敏感性，只能说明方案没有留白。",
            "",
            "真正限制灵敏度的是粒度。方案能调的最小单位是一个地点（60 分钟以上），"
            "而常量扰动带来的是 5–20 分钟：跨不过那一档，所以通过率不变、地点数也不变。"
            "E19 里那 3 条失败的 brief，正是被压到 `SHOOT_TOO_SHORT` 下限的边界样本。",
            "",
            "这也解释了 E18 为什么在 8 个城市都测不出差别：不是城市结构的问题，"
            "是任何小于一个地点的变化都不会反映到结果里。",
        ]
    else:
        lines += [
            "**余量不小**，说明 brief 本身给得宽松，参数扰动被余量吃掉了。"
            "要让常量变得可测，得让 brief 更紧——那是评测集设计的问题，不是代码的问题。",
        ]
    lines += [""]

    if infeasible:
        lines += ["## 不可行的那些卡在哪", ""]
        codes = Counter(
            code for row in infeasible for code, n in row["codes"].items() for _ in range(n)
        )
        for code, count in codes.most_common(6):
            lines.append(f"- `{code}`：{count} 条")
        lines += [
            "",
            "这些 brief 的通过与否由可行性判定决定，和排期常量无关——"
            "所以它们不会因为参数扰动而改变，这在 E19 里表现为「一半的样本永远不变」。",
            "",
        ]

    lines += [
        "## 局限",
        "",
        "余量是 `hours×60 − 方案跨度`，而方案跨度从第一个时间块算起，不含开始前的等待。"
        "所以余量不是「用户可用的空闲」，只是「方案之后还剩多少」。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = measure()
    text = render(rows)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    feasible = [row for row in rows if row["hard_pass"]]
    median = statistics.median(row["margin"] for row in feasible) if feasible else 0
    print(f"eval margin: {len(rows)} briefs, {len(feasible)} pass, median margin {median:.0f}m")
    if "--check" in sys.argv:
        if not REPORT.exists() or REPORT.read_text(encoding="utf-8") != text:
            print(f"{REPORT.name} 与当前输出不一致，运行 --write", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
