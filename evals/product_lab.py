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
MIN_HUMAN_ACCURACY = 0.90


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


def _human_cases() -> list[dict[str, Any]]:
    """How people actually type, with ground truth labelled by hand.

    The template set above proves nothing was mis-mapped; it cannot prove the
    parser survives spoken numerals (八百块 / 三小时), postfix budgets
    (2000 预算), ranges (1200 到 1500) or implied company (带女朋友). These are
    regression cases: they were written first, the parser was fixed second.
    """
    rows = [
        ("国庆想带女朋友去西安拍唐装，大概一千五，拍个五六个小时", "xian", "tang", 6.0, 1500, 2),
        ("下个月去苏州，想拍宋制的，预算两千以内，半天就行", "suzhou", "song", 4.0, 2000, None),
        ("洛阳三天两夜，中间抽半天拍个汉服，800 块够吗", "luoyang", None, 4.0, 800, None),
        ("在南京拍明制，两个人，别超过 1200", "nanjing", "ming", None, 1200, 2),
        ("杭州西湖边上那种温柔的感觉，预算随便", "hangzhou", "song", None, None, None),
        ("去趟西安，想穿魏晋那种飘逸的，4 小时够拍吗", "xian", "weijin", 4.0, None, None),
        ("十一去洛阳，唐风，预算 1800，人越少越好", "luoyang", "tang", None, 1800, None),
        ("苏州园林里面拍民国旗袍，两个人，大概八百块", "suzhou", "republic", None, 800, 2),
        ("南京想拍点书卷气的，宋制那种，一整天", "nanjing", "song", 8.0, None, None),
        ("西安城墙，唐制，预算一千左右，上午拍完", "xian", "tang", None, 1000, None),
        ("杭州，明制，预算 1500，三个小时", "hangzhou", "ming", 3.0, 1500, None),
        ("洛阳龙门石窟附近拍一套，魏晋风，别太贵", "luoyang", "weijin", None, None, None),
        ("国庆西安，唐装，1500，6 小时，别去大唐芙蓉园", "xian", "tang", 6.0, 1500, None),
        ("苏州拍宋制，人少一点，预算 1000", "suzhou", "song", None, 1000, None),
        ("南京民国风，预算 900，4 小时，室内也行", "nanjing", "republic", 4.0, 900, None),
        ("西安明制，两个人，2000 预算，拍 5 小时", "xian", "ming", 5.0, 2000, 2),
        ("想拍唐制的，去西安，预算 1200 到 1500", "xian", "tang", None, 1500, None),
        ("杭州宋制，半天，预算 1.5k", "hangzhou", "song", 4.0, 1500, None),
        ("洛阳唐风写真，一千块，三小时", "luoyang", "tang", 3.0, 1000, None),
        ("苏州，魏晋，预算 800，随便拍多久", "suzhou", "weijin", None, 800, None),
        ("西安拍民国旗袍，预算 600，两小时", "xian", "republic", 2.0, 600, None),
        ("国庆去南京，宋制，1500，别拍夜景", "nanjing", "song", None, 1500, None),
        ("杭州，唐制，上午，预算 1000", "hangzhou", "tang", None, 1000, None),
        ("洛阳拍明制，预算 2200，6 小时，室内为主", "luoyang", "ming", 6.0, 2200, None),
        ("西安，宋制，600 块能拍吗", "xian", "song", None, 600, None),
        ("苏州，唐制，预算 1300，4 小时，别去虎丘", "suzhou", "tang", 4.0, 1300, None),
        ("南京，魏晋，一千八，一整天的感觉", "nanjing", "weijin", 8.0, 1800, None),
        ("杭州拍民国，两个人，预算 1100", "hangzhou", "republic", None, 1100, 2),
        ("洛阳宋制，5 小时，预算 1400", "luoyang", "song", 5.0, 1400, None),
        ("西安，随便什么风格，预算 1000，半天", "xian", None, 4.0, 1000, None),
    ]
    return [
        {"id": f"human-{index:02d}", "text": text, "city": city, "style": style,
         "hours": hours, "budget_cny": budget, "party": party}
        for index, (text, city, style, hours, budget, party) in enumerate(rows, 1)
    ]


def _holdout_cases() -> list[dict[str, Any]]:
    """Untouched holdout: written and labelled, then run WITHOUT tuning rules.

    If the parser is changed again to make these pass, they stop being a
    holdout. Their accuracy is reported but deliberately NOT a pass condition.
    """
    rows = [
        ("十月中旬去西安，拍套唐装，预算一千二，四小时左右", "xian", "tang", 4.0, 1200, 1),
        ("苏州，想拍宋制，预算不高，八百块", "suzhou", "song", None, 800, 1),
        ("南京两天，抽三小时拍民国，预算九百", "nanjing", "republic", 3.0, 900, 1),
        ("杭州拍魏晋，两个人，预算一千六，五小时", "hangzhou", "weijin", 5.0, 1600, 2),
        ("洛阳，明制，预算两千，一整天", "luoyang", "ming", 8.0, 2000, 1),
        ("国庆去西安，唐风，别超过一千八，六小时", "xian", "tang", 6.0, 1800, 1),
        ("带爸妈去苏州拍明制，三个人，预算两千五", "suzhou", "ming", None, 2500, 3),
        ("西安，宋制，预算一千，三个钟头", "xian", "song", 3.0, 1000, 1),
        ("杭州，民国旗袍，八百块，半天", "hangzhou", "republic", 4.0, 800, 1),
        ("洛阳唐制，两个人，一千五，五小时", "luoyang", "tang", 5.0, 1500, 2),
        ("南京，魏晋风，预算九百五", "nanjing", "weijin", None, 950, 1),
        ("苏州拍唐制，四小时，预算一千三", "suzhou", "tang", 4.0, 1300, 1),
        ("西安，随便拍拍，预算一千，两小时", "xian", None, 2.0, 1000, 1),
        ("杭州宋制，预算一千一，三小时", "hangzhou", "song", 3.0, 1100, 1),
        ("洛阳，民国，预算一千四，两个人，四小时", "luoyang", "republic", 4.0, 1400, 2),
    ]
    return [
        {"id": f"holdout-{index:02d}", "text": text, "city": city, "style": style,
         "hours": hours, "budget_cny": budget, "party": party}
        for index, (text, city, style, hours, budget, party) in enumerate(rows, 1)
    ]


HUMAN_FIELDS = ("city", "style", "hours", "budget_cny", "party")
HUMAN_LABELS = {
    "city": "城市", "style": "风格", "hours": "时长",
    "budget_cny": "预算", "party": "人数",
}


def measure_human_briefs(tools: KnowledgeTools) -> dict[str, Any]:
    """Score spoken briefs field by field; `None` in a case means "not specified"."""
    def run(cases: list[dict[str, Any]]) -> dict[str, Any]:
        totals = dict.fromkeys(HUMAN_FIELDS, 0)
        passed = dict.fromkeys(HUMAN_FIELDS, 0)
        failures: list[str] = []
        wrongly_labelled: list[str] = []
        for case in cases:
            parsed = understand(case["text"], today=TODAY, tools=tools)
            request = parsed.request
            got = {
                "city": request.city,
                "style": request.styles[0] if request.styles else None,
                "hours": float(request.hours),
                "budget_cny": int(request.budget_cny),
                "party": int(request.party_size),
            }
            for field in HUMAN_FIELDS:
                want = case.get(field)
                if want is None:
                    continue
                totals[field] += 1
                if got[field] == want:
                    passed[field] += 1
                else:
                    failures.append(f"{case['id']}:{field} got={got[field]} want={want}")
            # A field the user never mentioned must be marked inferred/default.
            stated = {a.field for a in parsed.assumptions if a.source == "stated"}
            for field, alias in (
                ("hours", "hours"), ("budget_cny", "budget_cny"), ("party", "party_size"),
            ):
                if case.get(field) is None and alias in stated:
                    wrongly_labelled.append(f"{case['id']}:{field} claimed as stated")
        accuracy = {
            field: round(passed[field] / totals[field], 4) if totals[field] else 1.0
            for field in HUMAN_FIELDS
        }
        return {
            "cases": len(cases), "totals": totals, "passed": passed,
            "accuracy": accuracy, "failures": failures,
            "wrongly_labelled": wrongly_labelled,
        }

    return {"human": run(_human_cases()), "holdout": run(_holdout_cases())}


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
    human: dict[str, Any],
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
        "## 真实措辞（回归集）",
        "",
        f"共 {human['human']['cases']} 句手写口语 brief：期望值先写、解析器后改，用于防退化。",
        "",
        "| 字段 | 通过 / 总数 | 准确率 |",
        "| --- | ---: | ---: |",
    ]
    for field in HUMAN_FIELDS:
        total = human["human"]["totals"][field]
        if not total:
            continue
        lines.append(
            f"| {HUMAN_LABELS[field]} | {human['human']['passed'][field]} / {total} | "
            f"{human['human']['accuracy'][field]:.1%} |"
        )
    lines += [
        "",
        f"门槛：每个字段 ≥ **{MIN_HUMAN_ACCURACY:.0%}**；用户没提的字段必须标成推断/默认，"
        f"谎称「明说」也记失败（当前 {len(human['human']['wrongly_labelled'])} 处）。",
        "",
        "## Holdout（只报告，不作为门槛）",
        "",
        f"另 {human['holdout']['cases']} 句写完后立即跑、**没有为它改过规则**。"
        "数字用来观察泛化；一旦为它调代码，它就不再是盲测。",
        "",
        "| 字段 | 通过 / 总数 | 准确率 |",
        "| --- | ---: | ---: |",
    ]
    for field in HUMAN_FIELDS:
        total = human["holdout"]["totals"][field]
        if not total:
            continue
        lines.append(
            f"| {HUMAN_LABELS[field]} | {human['holdout']['passed'][field]} / {total} | "
            f"{human['holdout']['accuracy'][field]:.1%} |"
        )
    lines += [
        "",
        "## 边界",
        "",
        "- 前 28 句由知识库官方名生成，验证的是「受支持的城市 / 风格不会被认错」，"
        "不代表真实措辞；真实措辞由上面的回归集与 holdout 覆盖。",
        "- 三段都不评价「哪种取舍更好」「用户是否愿意接受」，只验证不变量。",
        "",
        "## 失败明细",
        "",
    ]
    all_failures = (
        [f"understand/{item}" for item in understanding["failures"]]
        + [f"strategy/{item}" for item in strategies["failures"]]
        + [f"negotiation/{item}" for item in negotiation["failures"]]
        + [f"human/{item}" for item in human["human"]["failures"]]
        + [f"human/{item}" for item in human["human"]["wrongly_labelled"]]
        + [f"holdout/{item}" for item in human["holdout"]["failures"]]
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
    human = measure_human_briefs(tools)
    expected = render(understanding, human, strategies, negotiation)

    failed = (
        understanding["failures"]
        or strategies["failures"]
        or negotiation["failures"]
        or human["human"]["failures"]
        or human["human"]["wrongly_labelled"]
        or any(
            value < MIN_UNDERSTANDING_ACCURACY
            for value in understanding["accuracy"].values()
        )
        or any(
            value < MIN_HUMAN_ACCURACY
            for value in human["human"]["accuracy"].values()
        )
    )
    print(
        f"understanding: {understanding['cases']} cases, "
        f"accuracy={understanding['accuracy']}"
    )
    print(
        f"human briefs: {human['human']['cases']} cases, "
        f"accuracy={human['human']['accuracy']}, "
        f"failures={len(human['human']['failures'])}"
    )
    print(
        f"holdout (reported, not gated): {human['holdout']['cases']} cases, "
        f"accuracy={human['holdout']['accuracy']}"
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
