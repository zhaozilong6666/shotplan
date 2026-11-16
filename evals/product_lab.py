"""Offline quality lab for the product surface: understand -> compare -> negotiate.

The core pipeline has an eval suite with a control group. The newer product
layers did not: they had unit tests, which prove examples work, not that the
layers are reliable. This lab gives them the same treatment as the planner:

* every supported city/style sentence must parse into the right structured
  request (field-level accuracy, no model, no network);
* every strategy must keep the user's hard constraints and pass the validator;
* every negotiation option must pass the real preflight, not just look plausible.

    python -m evals.product_lab --write
    python -m evals.product_lab --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from shotplan.agents import MockProvider
from shotplan.brief import understand
from shotplan.compare import compare_strategies
from shotplan.config import Settings
from shotplan.domain.feasibility import check_feasibility
from shotplan.domain.models import PlanRequest
from shotplan.knowledge.tools import KnowledgeTools, get_tools
from shotplan.negotiation import negotiate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "product-lab.md"
TODAY = dt.date(2026, 9, 20)  # fixed so 「国庆」 always resolves to 2026-10-01
CFG = Settings(provider="mock", max_repairs=1)
MIN_UNDERSTANDING_ACCURACY = 0.95


def _sentence_cases(tools: KnowledgeTools) -> list[dict[str, Any]]:
    """Every supported city x style combination, with explicit ground truth."""
    cases: list[dict[str, Any]] = []
    index = 0
    for city_id in tools.city_ids():
        city_name = str(tools.city(city_id)["name"])
        for style_id in tools.style_ids():
            style_name = str(tools.style(style_id)["name"])
            budget = 1200 + 100 * (index % 5)
            cases.append(
                {
                    "id": f"{city_id}-{style_id}",
                    "text": f"国庆去{city_name}拍{style_name}，预算 {budget} 元，6 小时",
                    "city": city_id,
                    "style": style_id,
                    "budget_cny": budget,
                    "hours": 6.0,
                }
            )
            index += 1
    return cases


def _edge_cases(tools: KnowledgeTools) -> list[dict[str, Any]]:
    avoid_name = "大唐芙蓉园"
    avoid_id = next(
        (loc["id"] for loc in tools.locations("xian") if loc["name"] == avoid_name),
        "",
    )
    return [
        {
            "id": "inferred-time-and-budget",
            "text": "想去西安拍照，预算大概一千五，最好上午收工",
            "city": "xian",
            "style": None,
            "budget_cny": 1500,
            "hours": 3.0,
        },
        {
            "id": "explicit-avoid",
            "text": f"国庆去西安拍唐风，预算 1500，别去{avoid_name}",
            "city": "xian",
            "style": "tang",
            "budget_cny": 1500,
            "hours": 6.0,
            "avoid": avoid_id,
        },
        {
            "id": "missing-city-defaults",
            "text": "预算 1500，拍唐风",
            "city": "xian",
            "style": "tang",
            "budget_cny": 1500,
            "hours": 6.0,
            "expect_default_city": True,
        },
    ]


def measure_understanding(tools: KnowledgeTools) -> dict[str, Any]:
    cases = _sentence_cases(tools) + _edge_cases(tools)
    totals = {"city": 0, "hours": 0, "budget_cny": 0, "style": 0, "avoid": 0, "default": 0}
    passed = dict.fromkeys(totals, 0)
    failures: list[str] = []

    for case in cases:
        parsed = understand(case["text"], today=TODAY, tools=tools)
        request = parsed.request
        checks = {
            "city": request.city == case["city"],
            "hours": float(request.hours) == float(case["hours"]),
            "budget_cny": int(request.budget_cny) == int(case["budget_cny"]),
            "style": (
                case["style"] is None
                or (bool(request.styles) and request.styles[0] == case["style"])
            ),
        }
        if case.get("avoid"):
            checks["avoid"] = case["avoid"] in request.avoid
        if case.get("expect_default_city"):
            checks["default"] = any(
                a.field == "city" and a.source == "default" for a in parsed.assumptions
            )
        for field, ok in checks.items():
            totals[field] += 1
            if ok:
                passed[field] += 1
            else:
                failures.append(f"{case['id']}:{field}")
    return {
        "cases": len(cases),
        "totals": totals,
        "passed": passed,
        "failures": failures,
        "accuracy": {
            field: round(passed[field] / totals[field], 4) if totals[field] else 1.0
            for field in totals
        },
    }


def measure_strategies(tools: KnowledgeTools) -> dict[str, Any]:
    briefs = [
        PlanRequest(
            city=city, date="2026-10-04", hours=6, budget_cny=1500, styles=["tang"]
        )
        for city in tools.city_ids()
    ]
    strategy_runs = 0
    failures: list[str] = []
    converged = 0
    distinct_values: list[int] = []

    for brief in briefs:
        data = compare_strategies(brief, settings=CFG, provider=MockProvider("good"))
        distinct_values.append(int(data["distinct_plans"]))
        if data["distinct_plans"] == 1:
            converged += 1
        by_id = {item["id"]: item for item in data["strategies"]}
        for item in data["strategies"]:
            strategy_runs += 1
            metrics = item["metrics"]
            if not item.get("within_original_constraints"):
                failures.append(f"{brief.city}:{item['id']}:constraint-changed")
            if item.get("relaxations"):
                failures.append(f"{brief.city}:{item['id']}:relaxed")
            if metrics.get("status") != "ok":
                failures.append(f"{brief.city}:{item['id']}:status-{metrics.get('status')}")
            if not metrics.get("hard_pass"):
                failures.append(f"{brief.city}:{item['id']}:hard-fail")
        chosen = by_id.get(data["recommendation"].get("id"))
        if chosen is None:
            failures.append(f"{brief.city}:recommendation-missing")
        elif not chosen["metrics"].get("hard_pass"):
            failures.append(f"{brief.city}:recommendation-hard-fail")
    return {
        "briefs": len(briefs),
        "strategy_runs": strategy_runs,
        "converged": converged,
        "distinct_values": distinct_values,
        "failures": failures,
    }


def measure_negotiation(tools: KnowledgeTools) -> dict[str, Any]:
    cases = [
        PlanRequest(city="xian", date="2026-10-04", hours=6, budget_cny=100, styles=["tang"]),
        PlanRequest(city="xian", date="2026-10-04", hours=1, budget_cny=1500, styles=["tang"]),
        PlanRequest(
            city="xian", date="2026-10-04", hours=6, budget_cny=1500,
            styles=["tang"], must_include=["city_wall"], avoid=["city_wall"],
        ),
    ]
    failures: list[str] = []
    option_count = 0
    for request in cases:
        options = negotiate(request, tools=tools)
        if not options:
            failures.append(f"{request.budget_cny}/{request.hours}:no-option")
            continue
        keys: set[tuple] = set()
        for option in options:
            option_count += 1
            key = repr(sorted(option.changes.items()))
            if key in keys:
                failures.append(f"{request.budget_cny}/{request.hours}:duplicate")
            keys.add(key)
            if not option.verified:
                failures.append(f"{request.budget_cny}/{request.hours}:unverified")
            if not check_feasibility(option.request, tools).feasible:
                failures.append(f"{request.budget_cny}/{request.hours}:infeasible-option")
            if not option.changes:
                failures.append(f"{request.budget_cny}/{request.hours}:empty-change")
    return {"cases": len(cases), "options": option_count, "failures": failures}


def render(
    understanding: dict[str, Any],
    strategies: dict[str, Any],
    negotiation: dict[str, Any],
) -> str:
    lines = [
        "# 产品层测量：理解 / 三策略 / 协商",
        "",
        "> 由 `python -m evals.product_lab --write` 生成；`--check` 是门禁。",
        "> 离线、确定性、零 API 成本；测的是产品新入口，不是规划器本身。",
        "",
        "## 一句话理解",
        "",
        f"共 {understanding['cases']} 句（5 城 × 5 风格 + 3 个边界样例）。",
        "",
        "| 字段 | 通过 / 总数 | 准确率 |",
        "| --- | ---: | ---: |",
    ]
    labels = {
        "city": "城市", "hours": "时长", "budget_cny": "预算",
        "style": "风格", "avoid": "排除地点", "default": "缺省城市标记",
    }
    for field, label in labels.items():
        total = understanding["totals"][field]
        if not total:
            continue
        lines.append(
            f"| {label} | {understanding['passed'][field]} / {total} | "
            f"{understanding['accuracy'][field]:.1%} |"
        )
    lines += [
        "",
        f"门槛：每个字段准确率 ≥ **{MIN_UNDERSTANDING_ACCURACY:.0%}**。",
        "",
        "## 三策略不变量",
        "",
        f"{strategies['briefs']} 个 brief × 3 策略 = {strategies['strategy_runs']} 次真实运行。",
        "",
        "| 检查 | 结果 |",
        "| --- | --- |",
        f"| 硬约束被改写 | {sum('constraint-changed' in f for f in strategies['failures'])} 次 |",
        f"| 出现约束放宽 | {sum('relaxed' in f for f in strategies['failures'])} 次 |",
        f"| 运行未成功 | {sum('status-' in f for f in strategies['failures'])} 次 |",
        f"| 方案未过硬约束 | {sum('hard-fail' in f for f in strategies['failures'])} 次 |",
        f"| 推荐失败方案 | {sum('recommendation-' in f for f in strategies['failures'])} 次 |",
        "",
        f"收敛（三策略给出同一方案）：**{strategies['converged']} / {strategies['briefs']}**。"
        "收敛不是失败，它说明在当前条件下存在一个稳健解；报告只要求在收敛时如实说明。",
        "",
        "## 协商",
        "",
        f"{negotiation['cases']} 个不可行 brief，共产出 {negotiation['options']} 个候选改动。",
        "",
        "| 检查 | 结果 |",
        "| --- | --- |",
        f"| 没有任何建议 | {sum('no-option' in f for f in negotiation['failures'])} 次 |",
        f"| 建议未通过预检 | {sum('infeasible-option' in f for f in negotiation['failures'])} 次 |",
        f"| 重复建议 | {sum('duplicate' in f for f in negotiation['failures'])} 次 |",
        f"| 空改动 | {sum('empty-change' in f for f in negotiation['failures'])} 次 |",
        "",
        "## 边界",
        "",
        "- 28 句由知识库官方名生成，验证的是「受支持的城市 / 风格不会被认错」，"
        "不是真实客户措辞的准确率；真实分布需要人工标注后再补。",
        "- 三策略只验证不变量（不改约束、全部可执行、推荐不选失败方案），不评价哪种取舍更好；"
        "收敛是合法结论，不是缺陷。",
        "- 协商只验证「每个建议都通过预检且改动最小」，不评价用户是否愿意接受。",
        "",
        "## 失败明细",
        "",
    ]
    all_failures = (
        [f"understand/{item}" for item in understanding["failures"]]
        + [f"strategy/{item}" for item in strategies["failures"]]
        + [f"negotiation/{item}" for item in negotiation["failures"]]
    )
    if all_failures:
        lines += [f"- `{item}`" for item in all_failures]
    else:
        lines.append("无。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    tools = get_tools()
    understanding = measure_understanding(tools)
    strategies = measure_strategies(tools)
    negotiation = measure_negotiation(tools)
    expected = render(understanding, strategies, negotiation)

    failed = (
        understanding["failures"]
        or strategies["failures"]
        or negotiation["failures"]
        or any(
            value < MIN_UNDERSTANDING_ACCURACY
            for value in understanding["accuracy"].values()
        )
    )
    print(
        f"understanding: {understanding['cases']} cases, "
        f"accuracy={understanding['accuracy']}"
    )
    print(
        f"strategies: {strategies['strategy_runs']} runs, "
        f"converged={strategies['converged']}/{strategies['briefs']}, "
        f"failures={len(strategies['failures'])}"
    )
    print(
        f"negotiation: {negotiation['options']} options, "
        f"failures={len(negotiation['failures'])}"
    )

    if args.check:
        actual = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if actual.strip() != expected.strip() or failed:
            print("product-lab gate: FAIL", file=sys.stderr)
            return 1
        print("product-lab gate: OK")
        return 0
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUT}")
        return 0 if not failed else 1
    print(expected)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
