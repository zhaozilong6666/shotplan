"""Measure the real model on the same 80 cases the offline suite uses.

The offline suite proves the *harness* discriminates good plans from broken
ones. It cannot tell you how a real model behaves, because it never calls one.
This lab closes that gap: same cases, same validator, same scoring, real HTTP.

    python -m evals.real_model_lab --limit 80 --concurrency 4 --write
    python -m evals.real_model_lab --check

Why this is not a CI gate: it needs a paid key and the network, and the model
is sampled at temperature 0.2, so two runs legitimately differ. A gate that
fails at random teaches people to ignore gates. `--check` only verifies that
the committed report matches the committed measurements, which *is* deterministic.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from evals.run_eval import load_cases
from evals.scorers import CaseOutcome, evaluate_case, failed_case
from shotplan.agents import build_provider
from shotplan.agents.prompts import PROMPT_VERSION
from shotplan.config import get_settings
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "evals" / "real-model.json"
REPORT = ROOT / "docs" / "real-model.md"

# Cases that never reach the model: feasibility rejects them before generation.
# They are still run, because "the preflight works with a real provider wired
# in" is worth knowing, but they must not be counted as model successes.
_PREFLIGHT_ONLY = "infeasible"


def _run_one(case: dict[str, Any], settings: Any) -> CaseOutcome:
    started = time.perf_counter()
    try:
        request = PlanRequest.from_dict(case["request"])
        result = run_plan(request, provider=build_provider(), settings=settings, max_repairs=1)
        outcome = evaluate_case(case, result)
        if outcome.status == "error" and not outcome.error:
            # The pipeline degrades to a status instead of raising; without this
            # the record would say "error" and refuse to say why it happened.
            outcome.error = next(
                (i.message for i in result.validation.issues if i.code == "PIPELINE_ERROR"),
                "pipeline returned status=error",
            )
        outcome.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        return outcome
    except Exception as exc:  # a crashing case is a failed case, not a stopped suite
        outcome = failed_case(case, "real-model", f"{type(exc).__name__}: {exc}")
        outcome.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        return outcome


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def bootstrap_ci(values: list[float], rounds: int = 2000, seed: int = 20261113) -> tuple[float, float]:
    """Percentile bootstrap over cases, so the report states its own uncertainty.

    With n=69 the 95% interval on a pass rate is roughly ±5pp, which is wider
    than most differences people want to claim between models. Printing the
    interval next to the point estimate is the cheapest defence against
    reading noise as a ranking. Seeded, so regenerating the report is
    byte-identical.
    """
    if len(values) < 2:
        return (values[0] if values else 0.0, values[0] if values else 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(rounds):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return (means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1])


def summarise_repeats(
    runs: list[tuple[str, int, CaseOutcome]],
    settings: Any,
    repeat: int,
) -> dict[str, Any]:
    """Aggregate N repeats per case.

    Two uncertainties are reported separately, because they answer different
    questions and conflating them is how a single run gets over-trusted:

    * **case-level CI** — "would this result hold on another sample of briefs?"
      (bootstrap over cases, each case contributing its repeated mean)
    * **run-level spread** — "if I rerun the whole suite tomorrow, how far apart
      would the headline numbers be?" (one pass rate per repetition)

    The second one is the number people actually need when they compare two
    models from two single runs.
    """
    by_case: dict[str, list[tuple[int, CaseOutcome]]] = {}
    groups: dict[str, str] = {}
    for case_id, index, outcome in runs:
        by_case.setdefault(case_id, []).append((index, outcome))
        groups[case_id] = outcome.group

    planned_ids = [cid for cid in sorted(by_case) if groups[cid] != _PREFLIGHT_ONLY]
    per_case: list[dict[str, Any]] = []
    for cid in sorted(by_case):
        outcomes = [o for _, o in sorted(by_case[cid], key=lambda pair: pair[0])]
        if groups[cid] == _PREFLIGHT_ONLY:
            continue
        per_case.append(
            {
                "id": cid,
                "group": groups[cid],
                "repeat": len(outcomes),
                "pass_rate": round(sum(o.hard_pass for o in outcomes) / len(outcomes), 4),
                "mean_score": round(statistics.fmean(o.score for o in outcomes), 4),
                "error_rate": round(sum(1 for o in outcomes if o.error) / len(outcomes), 4),
                "max_repairs": max(o.repair_count for o in outcomes),
            }
        )

    def case_values(field: str) -> list[float]:
        return [float(record[field]) for record in per_case]

    pass_values = case_values("pass_rate")
    score_values = case_values("mean_score")
    rate_lo, rate_hi = bootstrap_ci(pass_values)
    score_lo, score_hi = bootstrap_ci(score_values)

    # One headline number per repetition: what a single-run report would say.
    run_pass: list[float] = []
    run_score: list[float] = []
    for index in range(repeat):
        flags, scores = [], []
        for cid in planned_ids:
            outcome = next(o for i, o in by_case[cid] if i == index)
            flags.append(1.0 if outcome.hard_pass else 0.0)
            scores.append(outcome.score)
        run_pass.append(sum(flags) / len(flags))
        run_score.append(statistics.fmean(scores))

    all_outcomes = [o for _, _, o in runs]
    errors = [o for o in all_outcomes if o.error]
    durations = [o.duration_ms for o in all_outcomes if not o.error]
    issue_codes: dict[str, int] = {}
    for outcome in all_outcomes:
        if outcome.group == _PREFLIGHT_ONLY:
            continue
        for code in outcome.issues:
            key = code.split(":")[0].strip()
            issue_codes[key] = issue_codes.get(key, 0) + 1

    return {
        "generated_by": "evals.real_model_lab",
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "prompt_version": PROMPT_VERSION,
        "temperature": 0.2,
        "cases": len(by_case),
        "repeat": repeat,
        "cases_needing_a_plan": len(per_case),
        "preflight_cases": len(by_case) - len(per_case),
        "preflight_refused": sum(
            1 for cid in by_case if groups[cid] == _PREFLIGHT_ONLY
            for _, o in by_case[cid] if o.status == "infeasible"
        ),
        "hard_pass_rate": round(statistics.fmean(pass_values), 4) if pass_values else 0.0,
        "hard_pass_ci": [round(rate_lo, 4), round(rate_hi, 4)],
        "mean_score": round(statistics.fmean(score_values), 4) if score_values else 0.0,
        "mean_score_ci": [round(score_lo, 4), round(score_hi, 4)],
        "min_score": round(min((r["mean_score"] for r in per_case), default=0.0), 4),
        "errors": len(errors),
        "error_rate": round(len(errors) / len(all_outcomes), 4) if all_outcomes else 0.0,
        "error_cases": sorted({o.case_id for o in errors}),
        # Keep the actual messages: an outage and a bad model both show up as
        # "error", and only the text tells them apart after the fact.
        "error_messages": dict(
            sorted(
                (msg[:120], sum(1 for o in errors if o.error[:120] == msg[:120]))
                for msg in {o.error[:120] for o in errors}
            )
        ),
        "first_attempt_clean": sum(1 for r in per_case if r["max_repairs"] == 0),
        "repaired": sum(1 for r in per_case if r["max_repairs"] > 0),
        "run_pass_rate": [round(value, 4) for value in run_pass],
        "run_mean_score": [round(value, 4) for value in run_score],
        "run_pass_spread_pp": round((max(run_pass) - min(run_pass)) * 100, 1) if run_pass else 0.0,
        "p50_ms": _pct(durations, 0.50),
        "p95_ms": _pct(durations, 0.95),
        "tokens_in": sum(o.tokens_in for o in all_outcomes),
        "tokens_out": sum(o.tokens_out for o in all_outcomes),
        "cost_cny": round(sum(o.cost_cny for o in all_outcomes), 4),
        "top_issue_codes": dict(sorted(issue_codes.items(), key=lambda kv: -kv[1])[:8]),
        "per_case": per_case,
    }


def summarise(outcomes: list[CaseOutcome], settings: Any) -> dict[str, Any]:
    planned = [o for o in outcomes if o.group != _PREFLIGHT_ONLY]
    preflight = [o for o in outcomes if o.group == _PREFLIGHT_ONLY]
    errors = [o for o in outcomes if o.error]
    hard_pass = sum(o.hard_pass for o in planned)
    durations = [o.duration_ms for o in outcomes if not o.error]
    issue_codes: dict[str, int] = {}
    for outcome in planned:
        for code in outcome.issues:
            code = code.split(":")[0].strip()
            issue_codes[code] = issue_codes.get(code, 0) + 1

    return {
        "generated_by": "evals.real_model_lab",
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "prompt_version": PROMPT_VERSION,
        "temperature": 0.2,
        "cases": len(outcomes),
        "cases_needing_a_plan": len(planned),
        "preflight_cases": len(preflight),
        "preflight_refused": sum(o.status == "infeasible" for o in preflight),
        "hard_pass": hard_pass,
        "hard_pass_rate": round(hard_pass / len(planned), 4) if planned else 0.0,
        "mean_score": round(statistics.fmean([o.score for o in planned]), 4) if planned else 0.0,
        # An error case scores 0.0, which is not a "worst plan" — it is no plan.
        # Mixing them would make the minimum look like a quality signal.
        "min_score": round(min((o.score for o in planned if not o.error), default=0.0), 4),
        "errors": len(errors),
        "error_rate": round(len(errors) / len(outcomes), 4) if outcomes else 0.0,
        "error_cases": sorted(o.case_id for o in errors),
        "error_messages": dict(
            sorted(
                (msg[:120], sum(1 for o in errors if o.error[:120] == msg[:120]))
                for msg in {o.error[:120] for o in errors}
            )
        ),
        # "First attempt produced parseable, usable output" is the number that
        # actually matters for structured output; repairs hide it in the score.
        "first_attempt_clean": sum(1 for o in planned if not o.error and o.repair_count == 0),
        "repaired": sum(1 for o in planned if o.repair_count > 0),
        "p50_ms": _pct(durations, 0.50),
        "p95_ms": _pct(durations, 0.95),
        "tokens_in": sum(o.tokens_in for o in outcomes),
        "tokens_out": sum(o.tokens_out for o in outcomes),
        "cost_cny": round(sum(o.cost_cny for o in outcomes), 4),
        "top_issue_codes": dict(sorted(issue_codes.items(), key=lambda kv: -kv[1])[:8]),
        "per_case": [
            {
                "id": o.case_id,
                "group": o.group,
                "status": o.status,
                "hard_pass": o.hard_pass,
                "score": round(o.score, 4),
                "repairs": o.repair_count,
                "duration_ms": o.duration_ms,
                "tokens_in": o.tokens_in,
                "tokens_out": o.tokens_out,
                "error": o.error[:200],
            }
            for o in sorted(outcomes, key=lambda o: o.case_id)
        ],
    }


def _source_label(path: str | Path) -> str:
    """Repo-relative POSIX path, so the same file always renders one header."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def build_report(
    snapshot: dict[str, Any],
    baseline: dict[str, Any] | None,
    source: str = "evals/real-model.json",
) -> str:
    planned = snapshot["cases_needing_a_plan"]
    clean_rate = snapshot["first_attempt_clean"] / planned if planned else 0.0
    lines = [
        "# 真实模型实测",
        "",
        f"命令：`python -m evals.real_model_lab --write` ｜ 数据源：`{_source_label(source)}` ｜ "
        "历史对照：[real-model-comparison.md](real-model-comparison.md)",
        "",
        f"模型 `{snapshot['model']}`（{snapshot['base_url']}，temperature "
        f"{snapshot['temperature']}，并发 {snapshot.get('concurrency', '?')}）｜ prompt "
        f"`{snapshot['prompt_version']}` ｜ "
        f"{snapshot['cases']} 个用例"
        + (f" × {snapshot['repeat']} 轮" if snapshot.get("repeat", 1) > 1 else "")
        + f"，其中 {planned} 个需要生成方案",
        "",
    ]
    if snapshot["cases"] < 80:
        lines += [
            f"> **部分取样**：本次只跑了主集 80 例中的 {snapshot['cases']} 例，"
            "样本构成与全量不同，不能与全量结果直接比较，也不代表整体通过率。",
            "",
        ]
    if snapshot.get("repeat", 1) > 1:
        rate_lo, rate_hi = snapshot["hard_pass_ci"]
        score_lo, score_hi = snapshot["mean_score_ci"]
    else:
        planned_cases = [c for c in snapshot["per_case"] if c["group"] != _PREFLIGHT_ONLY]
        # Repeat snapshots store per-case means; single-run ones store one
        # outcome. Accept both so a 1-repeat run cannot crash the renderer.
        score_lo, score_hi = bootstrap_ci(
            [float(c.get("score", c.get("mean_score", 0.0))) for c in planned_cases]
        )
        rate_lo, rate_hi = bootstrap_ci(
            [
                1.0
                if c.get("hard_pass", float(c.get("pass_rate", 0.0)) >= 0.5)
                else 0.0
                for c in planned_cases
            ]
        )
    lines += [
        "## 结论摘要",
        "",
        f"真实模型在 **{planned} 个需要生成方案的用例**上硬约束通过率 "
        f"**{snapshot['hard_pass_rate'] * 100:.1f}%**"
        f"（{snapshot['hard_pass_rate'] * planned:.0f}/{planned}，"
        f"95% CI {rate_lo * 100:.1f}–{rate_hi * 100:.1f}%），"
        f"平均得分 **{snapshot['mean_score']:.4f}**"
        f"（95% CI {score_lo:.4f}–{score_hi:.4f}），最低 {snapshot['min_score']:.4f}。",
        "",
        f"**首次输出即可用率 {clean_rate * 100:.1f}%**"
        f"（{snapshot['first_attempt_clean']}/{planned} 未触发修复），"
        f"发生修复 {snapshot['repaired']} 次，异常 {snapshot['errors']} 次。"
        "这个数字比平均分更能说明结构化输出的稳定性：修复循环会把一次失败掩盖成一个还不错的分数。"
        + (
            f" 异常用例：{', '.join(snapshot['error_cases'])}（流水线降级为 error，"
            "既不是好方案也不是坏方案，所以不计入最低分）。"
            if snapshot["error_cases"]
            else ""
        ),
        "",
        "| 指标 | 真实模型 | 离线规则 Agent | 对照基线 (naive) |",
        "| --- | ---: | ---: | ---: |",
    ]
    if baseline:
        lines.append(
            f"| 硬约束通过率 | {snapshot['hard_pass_rate'] * 100:.1f}% | "
            f"{baseline['hard_pass_rate'] * 100:.1f}% | "
            f"{baseline['naive_hard_pass_rate'] * 100:.1f}% |"
        )
        lines.append(
            f"| 平均得分 | {snapshot['mean_score']:.4f} | "
            f"{baseline['mean_score']:.4f} | {baseline['naive_mean_score']:.4f} |"
        )
        lines.append(
            f"| 平均耗时 | {snapshot['p50_ms'] / 1000:.1f}s (P50) | "
            f"{baseline['mean_duration_ms'] / 1000:.2f}s | — |"
        )
    else:
        lines.append(f"| 硬约束通过率 | {snapshot['hard_pass_rate'] * 100:.1f}% | — | — |")
        lines.append(f"| 平均得分 | {snapshot['mean_score']:.4f} | — | — |")

    if snapshot.get("repeat", 1) > 1:
        lines += [
            "",
            f"## 重复测量（{snapshot['repeat']} 轮）",
            "",
            "同一批用例重复执行，每轮单独算一次「如果只跑一轮会报出什么」：",
            "",
            "| 轮次 | 硬约束通过率 | 平均分 |",
            "| ---: | ---: | ---: |",
        ]
        for index, (rate, score) in enumerate(
            zip(
                snapshot["run_pass_rate"],
                snapshot["run_mean_score"],
                strict=True,
            ), start=1
        ):
            lines.append(f"| {index} | {rate * 100:.1f}% | {score:.4f} |")
        lines += [
            f"| **极差** | **{snapshot['run_pass_spread_pp']:.1f} pp** | — |",
            "",
            f"单轮之间的通过率极差是 **{snapshot['run_pass_spread_pp']:.1f} 个百分点**。"
            "这意味着：两次单轮实验之间小于这个幅度的差异，无法区分是配置变化还是采样噪声。"
            "上面的 95% CI 衡量的是「换一批用例会不会翻」，不是「换一轮会不会翻」，两者回答的不是同一个问题。",
        ]

    lines += [
        "",
        "## 成本与延迟",
        "",
        f"- 总 token：输入 {snapshot['tokens_in']:,} ／ 输出 {snapshot['tokens_out']:,}",
        f"- 总成本：¥{snapshot['cost_cny']:.2f}（{snapshot['cases']} 个用例，含失败的调用）",
        f"- 单次 P50：{snapshot['p50_ms'] / 1000:.1f}s ／ P95：{snapshot['p95_ms'] / 1000:.1f}s",
        (
            f"- 前置可行性判定在模型之前执行：{snapshot['preflight_cases']} 个不可行用例中 "
            f"{snapshot['preflight_refused']} 个被直接拒绝，未消耗生成调用"
            if snapshot["preflight_cases"]
            else "- 本次取样未包含不可行用例，前置拒绝逻辑未被覆盖"
        ),
        "",
        "## 最常见的硬问题",
        "",
    ]
    if snapshot["top_issue_codes"]:
        lines += ["| 规则 | 触发次数 |", "| --- | ---: |"]
        lines += [f"| `{code}` | {count} |" for code, count in snapshot["top_issue_codes"].items()]
    else:
        lines.append("本轮没有触发任何硬问题。")

    if snapshot.get("error_messages"):
        lines += ["", "## 异常分布", "", "| 错误 | 次数 |", "| --- | ---: |"]
        lines += [
            f"| `{message}` | {count} |"
            for message, count in sorted(
                snapshot["error_messages"].items(), key=lambda kv: -kv[1]
            )[:6]
        ]
        lines += [
            "",
            "上游故障与模型失败在计数上无法区分，只能靠这里的原文分辨："
            "`INSUFFICIENT_BALANCE`、`401`、`timeout` 属于前者，"
            "字段缺失与非法枚举属于后者。",
        ]

    lines += [
        "",
        "## 这份数据不能证明什么",
        "",
        (
            f"- 本报告是同一模型、同一 prompt 的 {snapshot['repeat']} 轮重复测量；"
            "轮次之间的极差已经列在上表，但结论仍不能外推到其他模型。"
            if snapshot.get("repeat", 1) > 1
            else "- 单模型单轮采样：temperature 0.2 仍有随机性，重跑的数字会小幅波动；"
            "要比较两个 prompt，必须用同一批用例、同一模型、连续跑，并把两轮都记下来。"
        ),
        "- 报告里的 95% 区间是对**用例**做自助法重采样得到的（n=69 时通过率区间约 ±5 个百分点），"
        "它衡量的是「换一批同分布的用例，结论会不会翻」，不是「换个模型还成不成立」。"
        "两个模型的区间重叠时，不能仅凭点估计宣布谁更好。",
        "- 成本按 `agents/openai_compatible.py` 里的价目表估算，不是账单；只用于相对比较。"
        "价目表未收录的模型会退到默认价，可能与实际计费相差数倍。",
        "- 主集共 80 个用例，覆盖 5 城 × 多风格 × 预算/时段压力；本次实际取样见上方用例数，"
        "两者都不代表开放域真实需求分布。",
        "- 离线规则 Agent 的 100% 不是「更强」，而是它被设计为不可能违反硬约束；"
        "两者的差距说明的是**校验器有判别力**，不是模型好坏排名。",
        "",
    ]
    return "\n".join(lines)


def offline_baseline() -> dict[str, Any] | None:
    """Numbers from the offline suite, when it has been run locally."""
    path = ROOT / "evals" / "out" / "latest.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    good = data.get("good") or {}
    naive = data.get("naive") or {}
    if not good or not naive:
        return None
    return {
        "hard_pass_rate": float(good.get("hard_pass_rate", 0.0)),
        "mean_score": float(good.get("avg_score", good.get("mean_score", 0.0))),
        "mean_duration_ms": float(good.get("avg_duration_ms", good.get("mean_duration_ms", 0.0))),
        "naive_hard_pass_rate": float(naive.get("hard_pass_rate", 0.0)),
        "naive_mean_score": float(naive.get("avg_score", naive.get("mean_score", 0.0))),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="real-model evaluation lab")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each case N times (N>1 turns on run-to-run spread reporting)",
    )
    parser.add_argument(
        "--max-error-rate",
        type=float,
        default=0.25,
        help="abort without writing when the error rate exceeds this (0 disables)",
    )
    parser.add_argument(
        "--min-calls",
        type=int,
        default=8,
        help="how many calls to observe before the error-rate guard can fire",
    )
    parser.add_argument("--progress-every", type=int, default=10, help="print progress every N calls")
    parser.add_argument("--write", action="store_true", help="refresh snapshot and report")
    parser.add_argument("--check", action="store_true", help="verify the report matches the snapshot")
    parser.add_argument(
        "--render",
        action="store_true",
        help="rewrite the report from an existing snapshot without calling the model",
    )
    parser.add_argument("--out", type=Path, default=SNAPSHOT)
    parser.add_argument("--report-path", type=Path, default=REPORT)
    args = parser.parse_args(argv)

    if args.check:
        if not args.out.exists():
            print(f"missing snapshot: {args.out}", file=sys.stderr)
            return 1
        snapshot = json.loads(args.out.read_text(encoding="utf-8"))
        expected = build_report(snapshot, offline_baseline(), str(args.out))
        actual = args.report_path.read_text(encoding="utf-8") if args.report_path.exists() else ""
        if actual.strip() != expected.strip():
            print(f"report drift: regenerate with --write ({args.report_path})", file=sys.stderr)
            return 1
        print("real-model report is in sync with the snapshot")
        return 0

    if args.render:
        if not args.out.exists():
            print(f"missing snapshot: {args.out}", file=sys.stderr)
            return 1
        snapshot = json.loads(args.out.read_text(encoding="utf-8"))
        args.report_path.write_text(
            build_report(snapshot, offline_baseline(), str(args.out)), encoding="utf-8"
        )
        print(f"rendered {args.report_path} from {args.out}", file=sys.stderr)
        return 0

    settings = get_settings()
    if settings.provider == "mock":
        print(
            "SHOTPLAN_PROVIDER is mock: this lab needs a real endpoint. "
            "Set SHOTPLAN_PROVIDER=openai_compatible and a key in .env.",
            file=sys.stderr,
        )
        return 2

    cases = load_cases()
    if args.limit:
        cases = cases[: args.limit]
    repeat = max(1, args.repeat)
    print(
        f"running {len(cases)} cases × {repeat} repeat(s) against {settings.llm_model} …",
        file=sys.stderr,
    )
    started = time.perf_counter()
    jobs = [(case, index) for case in cases for index in range(repeat)]
    raw: list[tuple[str, int, CaseOutcome]] = []
    aborted_reason = ""
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [
            pool.submit(lambda job: (job[0]["id"], job[1], _run_one(job[0], settings)), job)
            for job in jobs
        ]
        try:
            for done, future in enumerate(as_completed(futures), start=1):
                raw.append(future.result())
                if done % args.progress_every == 0 or done == len(futures):
                    elapsed = time.perf_counter() - started
                    rate = done / elapsed
                    eta = (len(futures) - done) / rate if rate else 0.0
                    errors = sum(1 for _, _, o in raw if o.error)
                    print(
                        f"  {done}/{len(futures)} done  errors={errors}  "
                        f"elapsed={elapsed:.0f}s  eta={eta:.0f}s",
                        file=sys.stderr,
                    )
                # A dead endpoint looks exactly like a bad model: every call
                # fails, the report says 0%, and by then the budget is gone.
                # Stop early instead, and say why.
                if len(raw) >= args.min_calls and args.max_error_rate > 0:
                    errors = sum(1 for _, _, o in raw if o.error)
                    if errors / len(raw) > args.max_error_rate:
                        aborted_reason = (
                            f"error rate {errors}/{len(raw)} exceeded "
                            f"{args.max_error_rate:.0%}; last error: "
                            f"{next((o.error for _, _, o in reversed(raw) if o.error), '')[:160]}"
                        )
                        for pending in futures:
                            pending.cancel()
                        break
        except KeyboardInterrupt:
            aborted_reason = "interrupted"
            for pending in futures:
                pending.cancel()
    elapsed = time.perf_counter() - started

    if aborted_reason:
        print(
            f"ABORTED after {len(raw)}/{len(futures)} calls: {aborted_reason}\n"
            "No snapshot written: a run that measures an outage is worse than no run.",
            file=sys.stderr,
        )
        return 3

    snapshot = (
        summarise_repeats(raw, settings, repeat)
        if repeat > 1
        else summarise([outcome for _, _, outcome in raw], settings)
    )
    snapshot["concurrency"] = max(1, args.concurrency)
    snapshot["wall_seconds"] = round(elapsed, 1)

    print(
        f"hard_pass {snapshot['hard_pass_rate']:.3f} mean {snapshot['mean_score']:.4f} "
        f"clean {snapshot['first_attempt_clean']} errors {snapshot['errors']} "
        f"cost ¥{snapshot['cost_cny']:.2f} in {elapsed:.1f}s",
        file=sys.stderr,
    )
    if args.write:
        args.out.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        args.report_path.write_text(
            build_report(snapshot, offline_baseline(), str(args.out)), encoding="utf-8"
        )
        print(f"wrote {args.out} and {args.report_path}", file=sys.stderr)
    return 0 if snapshot["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
