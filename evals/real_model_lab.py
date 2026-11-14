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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
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

# Spending tiers. The full 3-repeat suite is a milestone measurement, not an
# edit loop: one run costs hours and real money, while most changes only need
# to know "did this break something obvious?".
PROFILES: dict[str, dict[str, int]] = {
    "smoke": {"limit": 8, "repeat": 1, "concurrency": 3},
    "iterate": {"limit": 30, "repeat": 1, "concurrency": 4},
    "full": {"limit": 0, "repeat": 3, "concurrency": 4},
}

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


def _repair_breakdown(outcomes: list[CaseOutcome]) -> tuple[dict[str, int], dict[str, int]]:
    """Which hard issues the repair loop rescued, and which it could not.

    A single "3 repairs" number hides the interesting part: a loop that fixes
    missing fields is not the same as one that fixes a budget overrun.
    """
    fixed: dict[str, int] = {}
    failed: dict[str, int] = {}
    for outcome in outcomes:
        if not outcome.repair_count:
            continue
        target = fixed if outcome.hard_pass else failed
        for code in outcome.initial_hard_codes:
            target[code] = target.get(code, 0) + 1
    return fixed, failed


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
                "mean_soft_issues": round(
                    statistics.fmean(o.soft_issue_count for o in outcomes), 2
                ),
                "initial_hard_codes": sorted(
                    {code for o in outcomes for code in o.initial_hard_codes}
                ),
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
        if flags:  # a suite of only-infeasible cases has no pass rate
            run_pass.append(sum(flags) / len(flags))
            run_score.append(statistics.fmean(scores))

    all_outcomes = [o for _, _, o in runs]
    fixed_codes, failed_codes = _repair_breakdown(all_outcomes)
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
        "prompt_version": settings.prompt_version or PROMPT_VERSION,
        "prompt_router": bool(getattr(settings, "prompt_router", False)),
        "temperature": 0.2,
        "cases": len(by_case),
        "repeat": repeat,
        "cases_needing_a_plan": len(per_case),
        "preflight_cases": sum(1 for cid in by_case if groups[cid] == _PREFLIGHT_ONLY),
        # Count cases, not calls: with 3 repeats the same refusal would
        # otherwise be reported as three separate refusals.
        "preflight_refused": len(
            {
                cid
                for cid in by_case
                if groups[cid] == _PREFLIGHT_ONLY
                for _, outcome in by_case[cid]
                if outcome.status == "infeasible"
            }
        ),
        "hard_pass_rate": round(statistics.fmean(pass_values), 4) if pass_values else 0.0,
        "hard_pass_ci": [round(rate_lo, 4), round(rate_hi, 4)],
        "mean_score": round(statistics.fmean(score_values), 4) if score_values else 0.0,
        "mean_score_ci": [round(score_lo, 4), round(score_hi, 4)],
        "min_score": round(
            min((r["mean_score"] for r in per_case if r["error_rate"] < 1.0), default=0.0), 4
        ),
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
        "repair_fixed_codes": fixed_codes,
        "repair_failed_codes": failed_codes,
        "run_pass_rate": [round(value, 4) for value in run_pass],
        "run_mean_score": [round(value, 4) for value in run_score],
        "run_pass_spread_pp": round((max(run_pass) - min(run_pass)) * 100, 1) if run_pass else 0.0,
        "p50_ms": _pct(durations, 0.50),
        "p95_ms": _pct(durations, 0.95),
        "tokens_in": sum(o.tokens_in for o in all_outcomes),
        "tokens_out": sum(o.tokens_out for o in all_outcomes),
        "cost_cny": round(sum(o.cost_cny for o in all_outcomes), 4),
        "top_issue_codes": dict(sorted(issue_codes.items(), key=lambda kv: -kv[1])[:8]),
        "soft_issue_rate": round(
            sum(1 for r in per_case if r.get("mean_soft_issues", 0) > 0) / len(per_case), 4
        ) if per_case else 0.0,
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
        "prompt_version": settings.prompt_version or PROMPT_VERSION,
        "prompt_router": bool(getattr(settings, "prompt_router", False)),
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
        "soft_issue_rate": round(
            sum(1 for o in planned if o.soft_issue_count) / len(planned), 4
        ) if planned else 0.0,
        "repair_fixed_codes": _repair_breakdown(planned)[0],
        "repair_failed_codes": _repair_breakdown(planned)[1],
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
                "initial_hard_codes": o.initial_hard_codes,
                "final_issue_codes": o.issues[:8],
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


def _checkpoint_meta(settings: Any, repeat: int) -> dict[str, Any]:
    return {
        "model": settings.llm_model,
        "prompt_version": settings.prompt_version or PROMPT_VERSION,
        "prompt_router": bool(getattr(settings, "prompt_router", False)),
        "repeat": repeat,
    }


def load_checkpoint(
    path: Path, meta: dict[str, Any]
) -> dict[tuple[str, int], CaseOutcome]:
    """Read finished calls from a previous attempt.

    A relay that dies at call 120 of 240 should cost one restart, not one full
    re-run. The metadata line is checked first: resuming a run under a
    different model would silently mix two populations into one number.
    """
    if not path.exists():
        return {}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return {}
    stored = json.loads(lines[0])
    if stored != meta:
        raise ValueError(
            f"checkpoint {path} was written for {stored}, refusing to mix it with {meta}"
        )
    done: dict[tuple[str, int], CaseOutcome] = {}
    for line in lines[1:]:
        record = json.loads(line)
        done[(record["case_id"], int(record["index"]))] = CaseOutcome(**record["outcome"])
    return done


def append_checkpoint(path: Path, meta: dict[str, Any], case_id: str, index: int, outcome: CaseOutcome) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8") as handle:
        if new_file:
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
        handle.write(
            json.dumps(
                {"case_id": case_id, "index": index, "outcome": asdict(outcome)},
                ensure_ascii=False,
            )
            + "\n"
        )


def stratified_sample(
    cases: list[dict[str, Any]], limit: int, seed: int = 20261113
) -> list[dict[str, Any]]:
    """Pick `limit` cases that keep the group × city mix of the full set.

    `cases[:limit]` is not a smaller experiment, it is a different one: the
    file is ordered by group, so the first 30 cases contain zero `normal`
    briefs. A cheap run that silently changes the population is worse than an
    expensive one, because the number it produces still looks like a result.
    """
    if limit <= 0 or limit >= len(cases):
        return list(cases)

    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in cases:
        key = (case["group"], case["request"].get("city", "?"))
        strata.setdefault(key, []).append(case)
    keys = sorted(strata)
    total = len(cases)

    # Proportional allocation by largest remainder, at least one per stratum
    # when the budget allows it.
    exact = {key: len(strata[key]) * limit / total for key in keys}
    alloc = {key: int(exact[key]) for key in keys}
    if limit >= len(keys):
        for key in keys:
            alloc[key] = max(1, alloc[key])
    while sum(alloc.values()) > limit:
        key = max((k for k in keys if alloc[k] > 1), key=lambda k: alloc[k], default=None)
        if key is None:
            break
        alloc[key] -= 1
    while sum(alloc.values()) < limit:
        key = max(keys, key=lambda k: exact[k] - alloc[k])
        if alloc[key] >= len(strata[key]):
            break
        alloc[key] += 1

    rng = random.Random(seed)
    picked: list[dict[str, Any]] = []
    for key in keys:
        picked.extend(rng.sample(strata[key], min(alloc[key], len(strata[key]))))
    return sorted(picked, key=lambda case: case["id"])


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
        f"`{snapshot['prompt_version']}`"
        + (" + 路由（光线敏感用 v3）" if snapshot.get("prompt_router") else "")
        + f" ｜ "
        f"{snapshot['cases']} 个用例"
        + (f" × {snapshot['repeat']} 轮" if snapshot.get("repeat", 1) > 1 else "")
        + f"，其中 {planned} 个需要生成方案",
        "",
    ]
    if snapshot["cases"] < 80:
        if snapshot.get("cases_dir", "evals/cases") != "evals/cases":
            lines += [
                f"> **另一套题集**：本次跑的是 `{snapshot['cases_dir']}` 的 "
                f"{snapshot['cases']} 例，不是主集抽样，两者不能直接比较。",
                "",
            ]
        else:
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
            f" 软问题涉及 {snapshot['soft_issue_rate'] * 100:.1f}% 的用例"
            f"（{sum(1 for case in snapshot.get('per_case', []) if case.get('mean_soft_issues', 0) > 0)}"
            f"/{planned}）——硬约束通过不等于拍得好看。"
            if snapshot.get("soft_issue_rate")
            else ""
        )
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
        f"- 总成本：¥{snapshot['cost_cny']:.2f}"
        + (
            f"（{snapshot['cases']} 个用例 × {snapshot['repeat']} 轮，含失败的调用）"
            if snapshot.get("repeat", 1) > 1
            else f"（{snapshot['cases']} 个用例，含失败的调用）"
        ),
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

    fixed_codes = snapshot.get("repair_fixed_codes") or {}
    failed_codes = snapshot.get("repair_failed_codes") or {}
    if fixed_codes or failed_codes:
        lines += [
            "",
            "## 修复循环救回了什么",
            "",
            "| 初始硬问题 | 修好 | 没修好 |",
            "| --- | ---: | ---: |",
        ]
        for code in sorted(
            set(fixed_codes) | set(failed_codes),
            key=lambda c: (-fixed_codes.get(c, 0), c),
        ):
            lines.append(
                f"| `{code}` | {fixed_codes.get(code, 0)} | {failed_codes.get(code, 0)} |"
            )
        lines += [
            "",
            "「修好」= 该用例修复后通过硬约束；「没修好」= 修了但最终仍失败。"
            "只有初始错误码被记录时才会计入，所以老快照可能为空。",
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
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument(
        "--sample",
        choices=("stratified", "first"),
        default="stratified",
        help="how --limit picks cases: keep the group×city mix (default) or take the head",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=None,
        help="spending tier: smoke (8×1), iterate (30×1), full (80×3)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=None,
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
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("evals/out/checkpoint.jsonl"),
        help="append each finished call here so an interrupted run can resume",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=None,
        help="run a different case set, e.g. evals/redteam",
    )
    parser.add_argument("--progress-every", type=int, default=10, help="print progress every N calls")
    parser.add_argument(
        "--retry-errors",
        type=int,
        default=1,
        help="after the main pass, re-run calls that failed (flaky relays)",
    )
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

    cases = load_cases(args.cases_dir)
    profile = PROFILES.get(args.profile or "", {})
    limit = args.limit if args.limit is not None else profile.get("limit", 0)
    repeat = max(1, args.repeat if args.repeat is not None else profile.get("repeat", 1))
    concurrency = max(
        1, args.concurrency if args.concurrency is not None else profile.get("concurrency", 4)
    )
    if limit:
        cases = (
            stratified_sample(cases, limit)
            if args.sample == "stratified"
            else cases[:limit]
        )
    calls = len(cases) * repeat
    print(
        f"running {len(cases)} cases × {repeat} repeat(s) = {calls} calls "
        f"(concurrency {concurrency}, sample={args.sample}) against {settings.llm_model} …",
        file=sys.stderr,
    )
    started = time.perf_counter()
    jobs = [(case, index) for case in cases for index in range(repeat)]
    raw: list[tuple[str, int, CaseOutcome]] = []
    aborted_reason = ""

    meta = _checkpoint_meta(settings, repeat)
    done_calls: dict[tuple[str, int], CaseOutcome] = {}
    if args.checkpoint:
        try:
            done_calls = load_checkpoint(args.checkpoint, meta)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if done_calls:
            raw = [(cid, index, outcome) for (cid, index), outcome in done_calls.items()]
            jobs = [job for job in jobs if (job[0]["id"], job[1]) not in done_calls]
            print(
                f"resuming: {len(done_calls)} call(s) already done, {len(jobs)} left",
                file=sys.stderr,
            )
    write_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(lambda job: (job[0]["id"], job[1], _run_one(job[0], settings)), job)
            for job in jobs
        ]
        try:
            for done, future in enumerate(as_completed(futures), start=1):
                cid, index, outcome = future.result()
                raw.append((cid, index, outcome))
                if args.checkpoint and not outcome.error:
                    with write_lock:
                        append_checkpoint(args.checkpoint, meta, cid, index, outcome)
                if done % args.progress_every == 0 or done == len(futures):
                    elapsed = time.perf_counter() - started
                    rate = done / elapsed
                    eta = (len(futures) - done) / rate if rate else 0.0
                    errors = sum(1 for _, _, o in raw if o.error)
                    print(
                        f"  {len(done_calls) + done}/{len(done_calls) + len(futures)} done  "
                        f"errors={errors}  elapsed={elapsed:.0f}s  eta={eta:.0f}s",
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

    # Flaky relays fail in bursts. Re-running only the failed calls is much
    # cheaper than re-running the batch, and it keeps the surviving sample
    # instead of trading it for a fresh one with a different error rate.
    if not aborted_reason and args.retry_errors > 0:
        by_id = {case["id"]: case for case in cases}
        for round_index in range(args.retry_errors):
            failed = [(by_id[cid], index) for cid, index, o in raw if o.error and cid in by_id]
            if not failed:
                break
            print(
                f"  retrying {len(failed)} failed call(s), round {round_index + 1}",
                file=sys.stderr,
            )
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                retried = list(
                    pool.map(
                        lambda job: (job[0]["id"], job[1], _run_one(job[0], settings)),
                        failed,
                    )
                )
            merged = {(cid, index): (cid, index, outcome) for cid, index, outcome in raw}
            recovered = 0
            for cid, index, outcome in retried:
                if not outcome.error:
                    merged[(cid, index)] = (cid, index, outcome)
                    recovered += 1
            raw = list(merged.values())
            print(f"  recovered {recovered}/{len(retried)}", file=sys.stderr)

    elapsed = time.perf_counter() - started

    if aborted_reason:
        print(
            f"ABORTED after {len(raw)} calls: {aborted_reason}\n"
            "Checkpoint kept: rerun the same command to resume instead of restarting.",
            file=sys.stderr,
        )
        return 3

    snapshot = (
        summarise_repeats(raw, settings, repeat)
        if repeat > 1
        else summarise([outcome for _, _, outcome in raw], settings)
    )
    snapshot["concurrency"] = concurrency
    snapshot["wall_seconds"] = round(elapsed, 1)
    snapshot["sampled_case_ids"] = [case["id"] for case in cases]
    snapshot["cases_dir"] = str(args.cases_dir) if args.cases_dir else "evals/cases"

    if args.checkpoint and args.checkpoint.exists():
        args.checkpoint.unlink()
        print(f"checkpoint cleared: {args.checkpoint}", file=sys.stderr)

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
