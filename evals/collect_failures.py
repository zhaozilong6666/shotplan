"""Build a structured failure-case library from the control provider's output.

A quality system is only as good as the mistakes it has actually seen. This
script runs the deliberately flawed `naive` provider over every satisfiable
brief and records each hard-constraint violation with: the input brief, the
observed output fragment, the expected rule and the repair suggestion.

The generated files are deterministic (no timestamps), so a real-model failure
can later be appended in exactly the same shape and reviewed in a PR. See
docs/failure-gallery.md.

Usage:
    python -m evals.collect_failures --write-report
    python -m evals.collect_failures                 # summary only
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evals.run_eval import load_cases
from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "failures" / "cases.jsonl"
DEFAULT_GALLERY = ROOT / "docs" / "failure-gallery.md"


def _observed_slots(result: Any, limit: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "start": slot.start,
            "end": slot.end,
            "activity": slot.activity,
            "location_id": slot.location_id,
            "cost_cny": slot.cost_cny,
        }
        for slot in result.plan.slots[:limit]
    ]


def collect_failures() -> list[dict[str, Any]]:
    cases = [c for c in load_cases() if not c.get("expect_infeasible")]
    provider = MockProvider("naive")
    records: list[dict[str, Any]] = []
    for case in cases:
        result = run_plan(
            PlanRequest.from_dict(case["request"]), provider=provider, max_repairs=0
        )
        if result.validation.hard_pass:
            continue
        for issue in result.validation.hard_issues:
            records.append(
                {
                    "case_id": case["id"],
                    "group": case["group"],
                    "brief": case["request"],
                    "code": issue.code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "expected": issue.expected,
                    "suggestion": issue.suggestion,
                    "observed_slots": _observed_slots(result),
                    "declared_total_cny": result.plan.total_cost_cny,
                    "score": round(result.validation.score, 4),
                }
            )
    return records


def check_records(
    records: list[dict[str, Any]], min_records: int = 100, min_codes: int = 4
) -> list[str]:
    """CI gate: the failure library must stay populated and actionable."""
    problems: list[str] = []
    if len(records) < min_records:
        problems.append(
            f"only {len(records)} failure records, expected >= {min_records}"
        )
    codes = {record["code"] for record in records}
    if len(codes) < min_codes:
        problems.append(
            f"only {len(codes)} distinct codes, expected >= {min_codes}: {sorted(codes)}"
        )
    incomplete = [
        record["case_id"]
        for record in records
        if not record["expected"] or not record["suggestion"]
    ]
    if incomplete:
        problems.append(
            f"{len(incomplete)} record(s) missing expected/suggestion, "
            f"e.g. {sorted(set(incomplete))[:3]}"
        )
    return problems


def build_gallery(records: list[dict[str, Any]]) -> str:
    counts = Counter(r["code"] for r in records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["code"]].append(record)

    lines = [
        "# 失败案例库",
        "",
        "这份清单不是 bug 列表，而是**校验器真实抓到过的错误模式**。",
        "它回答一个面试里常被追问的问题：「你怎么知道你的规则不是拍脑袋定的？」",
        "",
        f"当前共 **{len(records)}** 条记录，来自 `naive` 对照 provider 在 "
        f"**{len({r['case_id'] for r in records})}** 个可满足 brief 上的输出，覆盖 "
        f"**{len(counts)}** 类硬约束违规。",
        "",
        "## 按问题码汇总",
        "",
        "| 问题码 | 触发次数 | 涉及用例 | 典型修复建议 |",
        "| --- | ---: | ---: | --- |",
    ]
    for code, count in counts.most_common():
        items = grouped[code]
        lines.append(
            f"| `{code}` | {count} | {len({i['case_id'] for i in items})} | "
            f"{items[0]['suggestion']} |"
        )

    lines += ["", "## 典型案例", ""]
    for code, _count in counts.most_common():
        item = grouped[code][0]
        lines += [
            f"### `{code}`",
            "",
            f"- 用例：`{item['case_id']}`（{item['group']}）  ｜  该次得分 `{item['score']}`",
            f"- 判定：{item['message']}",
            f"- 期望：{item['expected']}",
            f"- 修复建议：{item['suggestion']}",
            "",
            "输入 brief：",
            "",
            "```json",
            json.dumps(item["brief"], ensure_ascii=False, sort_keys=True),
            "```",
            "",
            f"观察到的输出（声明总计 {item['declared_total_cny']} 元，时间块前 3 条）：",
            "",
            "```json",
            json.dumps(item["observed_slots"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]

    lines += [
        "## 这份清单怎么用",
        "",
        "1. **新增规则前**：先在这里找证据。没有真实失败案例支撑的规则，多半是过度设计。",
        "2. **接入真实模型后**：把模型犯的新错误按同样字段追加到 `evals/failures/cases.jsonl`，"
        "再补一条规则与一个评测用例。这样每条规则都能追溯到一次真实失败。",
        "3. **回归时**：改动校验器后重跑采集，若某类问题突然消失，要确认是修复生效还是规则失效"
        "——对照组仍在，所以「问题消失」本身也需要解释。",
        "",
        "## 边界",
        "",
        "当前记录全部来自**故意犯错的离线对照组**，用于验证判定链与文档格式；"
        "它们不代表真实模型的错误分布。真实模型失败案例待阶段 4 接入后补充。",
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.collect_failures --write-report",
        "```",
        "",
        "`evals/failures/cases.jsonl` 与本文件均由代码生成，请勿手工编辑。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="collect shotplan failure cases")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--cases-path", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_GALLERY)
    parser.add_argument("--check", action="store_true", help="CI gate on library coverage")
    args = parser.parse_args(argv)

    records = collect_failures()
    args.cases_path.parent.mkdir(parents=True, exist_ok=True)
    args.cases_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + "\n",
        encoding="utf-8",
    )
    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(build_gallery(records), encoding="utf-8")

    counts = Counter(r["code"] for r in records)
    print(f"failures: {len(records)} records, {len(counts)} distinct codes")
    for code, count in counts.most_common():
        print(f"  {code}: {count}")
    problems = check_records(records) if args.check else []
    if problems:
        print("FAILURE GATE FAILED:")
        for problem in problems:
            print(f"  - {problem}")
    elif args.check:
        print("failure gate: OK")
    if args.write_report:
        print(f"gallery: {args.report_path}")
    print(f"cases: {args.cases_path}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
