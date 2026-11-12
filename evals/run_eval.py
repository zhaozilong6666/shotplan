"""Run the eval suite and produce a reproducible quality report.

Usage:
    python -m evals.run_eval --write-report
    python -m evals.run_eval --compare evals/baselines/v1-naive.json
    python -m evals.run_eval --save-baseline evals/baselines/v1-naive.json

The suite always runs TWO providers over the same cases: the rule-based planner
and a deliberately flawed planner. If both score the same, the measurement is
broken and CI fails.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals import CASES_DIR, OUT_DIR, __doc__  # noqa: F401
from evals.scorers import aggregate, evaluate_case, failed_case, regression_check
from shotplan.agents import MockProvider
from shotplan.config import get_settings
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "eval-report.md"


def load_cases(cases_dir: Path | None = None) -> list[dict[str, Any]]:
    directory = cases_dir or CASES_DIR
    cases: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_no}: invalid JSON: {exc}") from exc
            if "id" not in case or "request" not in case:
                raise ValueError(f"{path.name}:{line_no}: case needs id and request")
            cases.append(case)
    if not cases:
        raise ValueError(f"no eval cases found in {directory}")
    seen = set()
    for case in cases:
        if case["id"] in seen:
            raise ValueError(f"duplicate case id: {case['id']}")
        seen.add(case["id"])
    return cases


def run_suite(cases: list[dict[str, Any]], mode: str) -> list[Any]:
    provider = MockProvider(mode=mode)
    outcomes = []
    for case in cases:
        try:
            request = PlanRequest.from_dict(case["request"])
            result = run_plan(request, provider=provider, max_repairs=1)
            outcomes.append(evaluate_case(case, result))
        except Exception as exc:  # a crashing case is a failed case, not a stopped suite
            outcomes.append(failed_case(case, f"mock-{mode}", f"{type(exc).__name__}: {exc}"))
    return outcomes


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def build_report(
    good: dict[str, Any],
    naive: dict[str, Any],
    good_cases: list[Any],
    naive_cases: list[Any],
    comparison: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# shotplan 评测报告",
        "",
        f"生成时间：{now}  ｜  命令：`python -m evals.run_eval --write-report`",
        "",
        "## 结论摘要",
        "",
        f"规则 Agent 在 **{good['case_count']} 个用例**上硬约束通过率 "
        f"**{_fmt_pct(good['hard_pass_rate'])}**，平均得分 **{good['avg_score']}**；"
        f"故意犯错的对照基线为 **{_fmt_pct(naive['hard_pass_rate'])}** / "
        f"**{naive['avg_score']}**。差值 "
        f"**{round(good['avg_score'] - naive['avg_score'], 4)}** 分说明校验器与打分器"
        "确实区分了可执行与不可执行的方案，而不是给所有输出打高分。",
        "",
        "| 指标 | 规则 Agent | 对照基线 (naive) | 差值 |",
        "| --- | ---: | ---: | ---: |",
        f"| 硬约束通过率 | {_fmt_pct(good['hard_pass_rate'])} | {_fmt_pct(naive['hard_pass_rate'])} | "
        f"+{_fmt_pct(good['hard_pass_rate'] - naive['hard_pass_rate'])} |",
        f"| 平均得分 | {good['avg_score']} | {naive['avg_score']} | "
        f"+{round(good['avg_score'] - naive['avg_score'], 4)} |",
        f"| 最低得分 | {good['min_score']} | {naive['min_score']} | — |",
        f"| 用例通过数 | {good['cases_passed']}/{good['case_count']} | "
        f"{naive['cases_passed']}/{naive['case_count']} | — |",
        f"| 触发的硬问题总数 | {sum(o.hard_issue_count for o in good_cases)} | "
        f"{sum(o.hard_issue_count for o in naive_cases)} | — |",
        "",
        "## 方法",
        "",
        "1. 每个用例是一段**模糊客户 brief**（时长、预算、风格、日期、排除项），不是固定输出 fixture。",
        "2. 两个 provider 在**完全相同**的用例上各跑一遍；`naive` 故意忽略时间、转场、预算与溯源。",
        "3. 每份输出都经过同一个**不依赖模型的硬约束校验器**，由它给出问题清单与分项得分。",
        "4. 用例通过需同时满足：硬约束结论符合预期，且得分不低于该用例的分数下限。",
        "5. 维度权重：`constraints 0.40 / budget 0.20 / traceability 0.15 / light 0.15 / completeness 0.10`。",
        "",
        "对照组的意义：如果只对自己写的 planner 跑评测，很容易写出一套永远通过的评分器。"
        "把一份明确不可执行的方案放进去，是检验评测本身是否有效的控制组。",
        "",
        "## 分项平均得分",
        "",
        "| 维度 | 规则 Agent | 对照基线 |",
        "| --- | ---: | ---: |",
    ]
    for key in sorted(good["dimension_averages"]):
        lines.append(
            f"| {key} | {good['dimension_averages'][key]} | "
            f"{naive['dimension_averages'].get(key, 0.0)} |"
        )

    lines += [
        "", "## 分组结果", "",
        "| 分组 | 用例数 | 硬约束通过率 | 平均分 | 用例通过 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for group, stats in good["by_group"].items():
        lines.append(
            f"| {group} | {stats['cases']} | {_fmt_pct(stats['hard_pass_rate'])} | "
            f"{stats['avg_score']} | {stats['passed']}/{stats['cases']} |"
        )

    lines += [
        "", "## 逐用例明细（规则 Agent）", "",
        "| case | 分组 | 说明 | 硬约束 | 得分 | 问题 | 通过 |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for outcome in good_cases:
        issues = ", ".join(outcome.issues) or "—"
        lines.append(
            f"| {outcome.case_id} | {outcome.group} | {outcome.description} | "
            f"{'通过' if outcome.hard_pass else '未通过'} | {outcome.score} | "
            f"{issues} | {'是' if outcome.passed else '否'} |"
        )

    lines += ["", "## 高频问题（对照基线）", "", "| 规则码 | 触发次数 |", "| --- | ---: |"]
    for code, count in naive["issue_frequency"].items():
        lines.append(f"| {code} | {count} |")

    lines += [
        "",
        "## 回归阈值",
        "",
        f"- 平均分下限：`{thresholds['min_score']}`（当前 {good['avg_score']}）",
        f"- 硬约束通过率下限：`{_fmt_pct(thresholds['min_hard_pass_rate'])}`"
        f"（当前 {_fmt_pct(good['hard_pass_rate'])}）",
        f"- 用例失败数：`{good['cases_failed']}`",
        f"- 阈值结论：**{'通过' if not thresholds['problems'] else '未通过'}**",
    ]
    if thresholds["problems"]:
        lines += ["", "失败原因："] + [f"- {p}" for p in thresholds["problems"]]

    if comparison:
        lines += [
            "",
            "## 与已保存基线的对比",
            "",
            f"基线文件：`{comparison['file']}`",
            "",
            "| 指标 | 基线 | 本次 | 变化 |",
            "| --- | ---: | ---: | ---: |",
        ]
        for key in ("avg_score", "hard_pass_rate"):
            base_value = comparison["baseline"].get(key, 0.0)
            delta = comparison["current"][key] - base_value
            lines.append(
                f"| {key} | {base_value} | {comparison['current'][key]} | {delta:+.4f} |"
            )

    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "python -m evals.run_eval --write-report",
        "python -m evals.run_eval --compare evals/baselines/v1-naive.json",
        "```",
        "",
        "报告由代码生成，请勿手工编辑。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shotplan eval harness")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--compare", type=Path, help="baseline JSON to diff against")
    parser.add_argument("--save-baseline", type=Path, help="save naive metrics as baseline")
    parser.add_argument("--cases-dir", type=Path)
    parser.add_argument("--min-score", type=float)
    parser.add_argument("--min-hard-pass-rate", type=float, default=0.90)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "latest.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    cfg = get_settings()
    min_score = args.min_score if args.min_score is not None else cfg.eval_min_score
    cases = load_cases(args.cases_dir)

    good_cases = run_suite(cases, "good")
    naive_cases = run_suite(cases, "naive")
    good = aggregate(good_cases)
    naive = aggregate(naive_cases)

    comparison = None
    if args.compare and args.compare.exists():
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        baseline = baseline.get("naive", baseline)
        comparison = {
            "file": str(args.compare),
            "baseline": baseline,
            "current": naive,
        }

    problems = regression_check(good, min_score, args.min_hard_pass_rate)
    if good["avg_score"] <= naive["avg_score"]:
        problems.append("control group scored at or above the planner: measurement is broken")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thresholds": {
            "min_score": min_score,
            "min_hard_pass_rate": args.min_hard_pass_rate,
            "problems": problems,
        },
        "good": good,
        "naive": naive,
        "cases": {
            "good": [o.to_dict() for o in good_cases],
            "naive": [o.to_dict() for o in naive_cases],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.save_baseline:
        args.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.save_baseline.write_text(
            json.dumps({"naive": naive, "generated_at": payload["generated_at"]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.write_report:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(
            build_report(good, naive, good_cases, naive_cases, comparison,
                         payload["thresholds"]),
            encoding="utf-8",
        )

    if not args.quiet:
        print(f"cases: {good['case_count']}")
        print(f"good  : hard_pass={good['hard_pass_rate']}  avg_score={good['avg_score']}"
              f"  passed={good['cases_passed']}/{good['case_count']}")
        print(f"naive : hard_pass={naive['hard_pass_rate']}  avg_score={naive['avg_score']}")
        print(f"delta : {round(good['avg_score'] - naive['avg_score'], 4)}")
        if problems:
            print("REGRESSION:")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("thresholds: OK")
        if args.write_report:
            print(f"report: {args.report_path}")
        print(f"raw: {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
