"""Generate a gallery of real planner outputs as a static Markdown document.

Why a generated document instead of a screenshot: a reviewer should be able to
see what the agent actually produces — a full timeline, costs, alternatives,
assumptions and the validator's verdict — without installing anything or
holding an API key. Every value here comes from running the real pipeline.

Two rules make this document trustworthy:

* **Generated, never edited.** ``--check`` regenerates the page and diffs it, so
  it cannot drift from the code the way a hand-written sample would.
* **Only deterministic fields.** Plan ids, request ids, run ids and timings are
  random or machine-dependent, so they are deliberately excluded: the gallery
  must be byte-identical on every machine, otherwise the CI diff is noise.

Usage:
    python -m evals.examples --write
    python -m evals.examples --check      # CI: fail if the file is stale
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest, PlanResult
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "examples.md"

CASES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "happy",
        "西安 · 唐制 · 6 小时 · 1500 元",
        {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 1500, "styles": ["tang"]},
    ),
    (
        "conflict",
        "苏州 · 明制 · 排除两个最热门地点",
        {
            "city": "suzhou",
            "date": "2026-10-04",
            "hours": 6,
            "budget_cny": 1300,
            "styles": ["ming"],
            "avoid": ["humble_administrator_garden", "pingjiang_road"],
        },
    ),
    (
        "long",
        "南京 · 民国风 · 12 小时上限 · 4500 元",
        {"city": "nanjing", "date": "2026-10-17", "hours": 12, "budget_cny": 4500, "styles": ["republic"]},
    ),
    (
        "infeasible",
        "西安 · 预算 100 元（低于最低可执行成本）",
        {"city": "xian", "date": "2026-10-04", "hours": 6, "budget_cny": 100, "styles": ["tang"]},
    ),
)


def _brief_block(request: PlanRequest) -> str:
    payload = {
        "city": request.city,
        "date": request.date,
        "hours": request.hours,
        "budget_cny": request.budget_cny,
        "party_size": request.party_size,
        "styles": request.styles,
        "preferences": request.preferences,
        "must_include": request.must_include,
        "avoid": request.avoid,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return f"```json\n{body}\n```"


def _slot_table(result: PlanResult) -> str:
    if not result.plan.slots:
        return "（没有生成时间块）"
    rows = ["| 时间 | 环节 | 地点 | 花费 | 说明 |", "| --- | --- | --- | ---: | --- |"]
    for slot in result.plan.slots:
        note = slot.notes.replace("\n", " ")
        rows.append(
            f"| {slot.start}–{slot.end} | {slot.activity} | `{slot.location_id}` "
            f"| {slot.cost_cny} 元 | {note} |"
        )
    return "\n".join(rows)


def _validation_block(result: PlanResult) -> str:
    report = result.validation
    lines = [
        f"- 结论：**{'硬约束通过' if report.hard_pass else '硬约束未通过'}**，得分 **{report.score:.4f}**",
    ]
    if report.scores:
        parts = "、".join(f"{name} {value:.3f}" for name, value in sorted(report.scores.items()))
        lines.append(f"- 分项：{parts}")
    if report.issues:
        lines.append("- 问题清单：")
        for issue in report.issues:
            lines.append(f"  - `{issue.code}`（{issue.severity}）{issue.message}")
    else:
        lines.append("- 问题清单：无")
    return "\n".join(lines)


def _infeasible_block(result: PlanResult) -> str:
    feasibility = result.feasibility or {}
    reasons = feasibility.get("reasons", [])
    minimums = feasibility.get("minimums", {})
    rows = ["| 原因 | 说明 | 缺口 | 建议 |", "| --- | --- | --- | --- |"]
    for reason in reasons:
        rows.append(
            f"| `{reason['code']}` | {reason['message']} | {reason['shortfall']} | {reason['suggestion']} |"
        )
    lines = ["\n".join(rows)]
    if minimums:
        lines.append(
            "\n理论下限："
            f"预算 {minimums.get('min_budget_cny')} 元、时长 {minimums.get('min_hours')} 小时、"
            f"最低妆造 {minimums.get('min_styling_cny')} 元、转场 {minimums.get('transfer_cost_cny')} 元。"
        )
    return "\n".join(lines)


def render_case(name: str, title: str, result: PlanResult) -> str:
    lines = [f"## {title}", "", "**输入 brief**", "", _brief_block(result.request), ""]
    if result.status == "infeasible":
        lines += [
            f"**输出：结构化拒绝（`status={result.status}`）**",
            "",
            _infeasible_block(result),
            "",
            "**为什么这样处理**：这类 brief 在数学上不可能满足，硬凑一份方案只会产出"
            "「看起来合理但无法执行」的行程。拒绝发生在调用 provider 之前，所以两个 provider "
            "会给出同一个结论（见 [ADR 0007](adr/0007-feasibility-before-generation.md)）。",
            "",
        ]
    else:
        lines += ["**输出：可执行方案**", "", _slot_table(result), ""]
        styling = result.plan.styling
        if styling:
            lines.append(
                f"妆造：**{styling.get('name', styling.get('style_id', '未知'))}**，"
                f"发型 {styling.get('makeup_minutes')} 分钟、着装 {styling.get('costume_minutes')} 分钟。"
            )
        lines.append(f"总花费：**{result.plan.total_cost_cny} 元**（预算 {result.request.budget_cny} 元）。")
        if result.plan.alternatives:
            lines += ["", "备选方案："]
            for alt in result.plan.alternatives:
                lines.append(
                    f"- `{alt.get('trigger')}`：{alt.get('description')}（`{alt.get('location_id')}`）"
                )
        if result.plan.assumptions:
            lines += ["", "假设："]
            lines += [f"- {item}" for item in result.plan.assumptions]
        lines += ["", "**校验结论**", "", _validation_block(result), ""]
    lines += ["<details><summary>运行轨迹（步骤顺序，不含耗时）</summary>", ""]
    lines += [f"{index}. `{event.name}`" for index, event in enumerate(result.trace.events, 1)]
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def build_document() -> str:
    header = [
        "# 真实输出示例",
        "",
        "> 由 `python -m evals.examples --write` 生成，请勿手工编辑。",
        "> 每个示例都是**实际跑一遍流水线**的结果；只展示确定性字段，所以本文件在任何机器上都应逐字节一致。",
        "",
        "四个示例分别覆盖：正常需求、带排除约束的需求、时长上限、以及**数学上不可满足**的需求。",
        "看最后一节：没有时间块，只有原因、缺口与建议——那正是「拒绝」比「硬凑」更有用的地方。",
        "",
    ]
    provider = MockProvider("good")
    body: list[str] = []
    for name, title, brief in CASES:
        request = PlanRequest.from_dict(brief)
        result = run_plan(request, provider=provider, max_repairs=1)
        body.append(render_case(name, title, result))
    return "\n".join(header + body).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate the static example gallery")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true", help="CI: fail if the file is stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    document = build_document()
    if args.check:
        if not args.out.exists():
            print(f"examples gate FAILED: {args.out} does not exist")
            return 1
        if args.out.read_text(encoding="utf-8") != document:
            print(f"examples gate FAILED: {args.out} is stale; run python -m evals.examples --write")
            return 1
        print("examples gate: OK")
        return 0

    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(document, encoding="utf-8")
        print(f"wrote {args.out} ({len(document)} chars)")
    else:
        print(document[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
