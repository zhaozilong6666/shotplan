"""Aggregation and regression checks for eval runs.

A case passes only when BOTH the deterministic hard constraints and the
per-case quality floor are met. Keeping them separate matters: a plan can be
executable but low quality, and the eval should say which.

For unsatisfiable briefs, the CORRECT behaviour is a structured refusal, not a
plan. Those cases score 1.0 only when the pipeline identifies infeasibility and
explains why; hard-coding a fake plan scores 0.0. Since the preflight check is
deterministic and runs before generation, both providers share this verdict.

All float aggregation uses math.fsum so reported metrics are identical on every
supported Python version (see ADR 0006).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from math import fsum
from typing import Any

from shotplan.pipeline import PlanResult


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return round(ordered[index], 2)


@dataclass
class CaseOutcome:
    case_id: str
    group: str
    description: str
    provider: str
    hard_pass: bool
    score: float
    scores: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    hard_issue_count: int = 0
    duration_ms: float = 0.0
    repair_count: int = 0
    expected_hard_pass: bool = True
    expected_min_score: float = 0.0
    passed: bool = False
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trace_totals(result: PlanResult) -> tuple[int, int]:
    events = result.trace.events
    return (
        sum(event.tokens_in for event in events),
        sum(event.tokens_out for event in events),
    )


def evaluate_case(case: dict[str, Any], result: PlanResult) -> CaseOutcome:
    tokens_in, tokens_out = _trace_totals(result)
    common = {
        "case_id": case["id"],
        "group": case.get("group", "default"),
        "description": case.get("description", ""),
        "provider": result.trace.provider,
        "duration_ms": round(result.trace.total_duration_ms, 2),
        "repair_count": result.trace.repair_count,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_cny": round(result.trace.total_cost_cny, 6),
        "status": getattr(result, "status", "ok"),
        "error": "",
    }

    if bool(case.get("expect_infeasible", False)):
        feasibility = getattr(result, "feasibility", None) or {}
        identified = common["status"] == "infeasible" and bool(feasibility.get("reasons"))
        return CaseOutcome(
            **common,
            hard_pass=identified,
            score=1.0 if identified else 0.0,
            scores={},
            issues=["INFEASIBLE"] if identified else ["MISSED_INFEASIBLE"],
            hard_issue_count=0 if identified else 1,
            expected_hard_pass=False,
            expected_min_score=0.0,
            passed=identified,
        )

    expected_hard = bool(case.get("expect_hard_pass", True))
    expected_score = float(case.get("expect_min_score", 0.0))
    hard_ok = result.validation.hard_pass == expected_hard
    score_ok = result.validation.score >= expected_score
    return CaseOutcome(
        **common,
        hard_pass=result.validation.hard_pass,
        score=round(result.validation.score, 4),
        scores={k: round(v, 4) for k, v in result.validation.scores.items()},
        issues=[i.code for i in result.validation.issues],
        hard_issue_count=len(result.validation.hard_issues),
        expected_hard_pass=expected_hard,
        expected_min_score=expected_score,
        passed=bool(hard_ok and score_ok),
    )


def failed_case(case: dict[str, Any], provider: str, error: str) -> CaseOutcome:
    return CaseOutcome(
        case_id=case["id"],
        group=case.get("group", "default"),
        description=case.get("description", ""),
        provider=provider,
        hard_pass=False,
        score=0.0,
        expected_hard_pass=bool(case.get("expect_hard_pass", True)),
        expected_min_score=float(case.get("expect_min_score", 0.0)),
        passed=False,
        error=error,
    )


def aggregate(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    total = len(outcomes)
    if total == 0:
        return {"case_count": 0}

    dimensions: dict[str, list[float]] = defaultdict(list)
    for outcome in outcomes:
        for key, value in outcome.scores.items():
            dimensions[key].append(value)

    issue_counter: Counter[str] = Counter()
    for outcome in outcomes:
        issue_counter.update(outcome.issues)

    groups: dict[str, list[CaseOutcome]] = defaultdict(list)
    for outcome in outcomes:
        groups[outcome.group].append(outcome)

    by_group = {}
    for name, items in sorted(groups.items()):
        by_group[name] = {
            "cases": len(items),
            "hard_pass_rate": round(fsum(i.hard_pass for i in items) / len(items), 4),
            "avg_score": round(fsum(i.score for i in items) / len(items), 4),
            "passed": sum(i.passed for i in items),
        }

    durations = [o.duration_ms for o in outcomes]
    return {
        "case_count": total,
        "hard_pass_rate": round(fsum(o.hard_pass for o in outcomes) / total, 4),
        "cases_passed": sum(o.passed for o in outcomes),
        "cases_failed": total - sum(o.passed for o in outcomes),
        "avg_score": round(fsum(o.score for o in outcomes) / total, 4),
        "min_score": round(min(o.score for o in outcomes), 4),
        "avg_duration_ms": round(fsum(durations) / total, 2),
        "p50_duration_ms": _percentile(durations, 0.50),
        "p95_duration_ms": _percentile(durations, 0.95),
        "total_repairs": sum(o.repair_count for o in outcomes),
        "total_tokens_in": sum(o.tokens_in for o in outcomes),
        "total_tokens_out": sum(o.tokens_out for o in outcomes),
        "total_cost_cny": round(fsum(o.cost_cny for o in outcomes), 6),
        "dimension_averages": {
            k: round(fsum(v) / len(v), 4) for k, v in sorted(dimensions.items())
        },
        "issue_frequency": dict(issue_counter.most_common()),
        "by_group": by_group,
    }


def regression_check(
    metrics: dict[str, Any],
    min_score: float,
    min_hard_pass_rate: float = 0.90,
) -> list[str]:
    problems: list[str] = []
    if metrics["avg_score"] < min_score:
        problems.append(
            f"avg_score {metrics['avg_score']} is below threshold {min_score}"
        )
    if metrics["hard_pass_rate"] < min_hard_pass_rate:
        problems.append(
            f"hard_pass_rate {metrics['hard_pass_rate']} is below {min_hard_pass_rate}"
        )
    if metrics.get("cases_failed", 0) > 0:
        problems.append(f"{metrics['cases_failed']} case(s) failed their expectation")
    return problems


# The naive provider emits a plan with KNOWN defects. A green CI run is only
# meaningful if the validator still reports those defects. Without this check,
# disabling a single hard rule can go unnoticed: the control plan is so broken
# that its `constraints` score is already clamped at 0, so one fewer issue does
# not move the aggregate. See docs/adr/0009-gate-sensitivity.md.
CONTROL_RULE_MIN_COUNTS: dict[str, int] = {
    "TRANSFER_BUFFER": 100,   # observed 198 across 80 cases
    "UNKNOWN_LOCATION": 90,   # observed 186
    "BUDGET_EXCEEDED": 30,    # observed 69
    "TIME_OVERRUN": 30,       # observed 64
    "SHOOT_TOO_SHORT": 30,    # observed 69 (soft)
    "NO_ALTERNATIVE": 30,     # observed 69 (soft)
    "NO_SOURCE_REF": 30,      # observed 69 (soft)
}

MIN_CONTROL_DELTA = 0.45
MAX_CONTROL_AVG_SCORE = 0.45
MAX_CONTROL_HARD_PASS_RATE = 0.30


def control_group_check(
    naive: dict[str, Any],
    *,
    max_avg_score: float = MAX_CONTROL_AVG_SCORE,
    max_hard_pass_rate: float = MAX_CONTROL_HARD_PASS_RATE,
    min_rule_counts: dict[str, int] | None = None,
) -> list[str]:
    """Fail when the control group stops looking like a broken plan.

    Two aggregate ceilings plus a rule-level coverage floor. The floors are
    deliberately well below the observed counts: they must not drift when new
    eval cases are added, but they must still catch a disabled rule.
    """
    problems: list[str] = []
    if naive["avg_score"] > max_avg_score:
        problems.append(
            f"control avg_score {naive['avg_score']} exceeds {max_avg_score}: "
            "the validator may have been weakened"
        )
    if naive["hard_pass_rate"] > max_hard_pass_rate:
        problems.append(
            f"control hard_pass_rate {naive['hard_pass_rate']} exceeds "
            f"{max_hard_pass_rate}: invalid plans are being accepted"
        )
    observed = naive.get("issue_frequency", {})
    for code, floor in (min_rule_counts or CONTROL_RULE_MIN_COUNTS).items():
        count = observed.get(code, 0)
        if count < floor:
            problems.append(
                f"control rule {code} fired {count} times, expected >= {floor}: "
                "a validator rule appears to be disabled"
            )
    return problems


def eval_gate_problems(
    good: dict[str, Any],
    naive: dict[str, Any],
    min_score: float,
    min_hard_pass_rate: float = 0.90,
    *,
    min_delta: float = MIN_CONTROL_DELTA,
    control_check_kwargs: dict[str, Any] | None = None,
) -> list[str]:
    """The ONE gate used by CI and by the mutation lab.

    Keeping a single definition matters: the mutation lab injects sabotage and
    asserts this function rejects it. If the lab tested a copy, a later change
    to run_eval could silently open a hole.
    """
    problems = regression_check(good, min_score, min_hard_pass_rate)
    if good["avg_score"] <= naive["avg_score"]:
        problems.append("control group scored at or above the planner: measurement is broken")
    delta = round(good["avg_score"] - naive["avg_score"], 4)
    if delta < min_delta:
        problems.append(
            f"control delta {delta} is below {min_delta}: good and bad outputs "
            "are no longer distinguishable"
        )
    problems.extend(control_group_check(naive, **(control_check_kwargs or {})))
    return problems
