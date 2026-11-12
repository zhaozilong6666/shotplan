"""Aggregation and regression checks for eval runs.

A case passes only when BOTH the deterministic hard constraints and the
per-case quality floor are met. Keeping them separate matters: a plan can be
executable but low quality, and the eval should say which.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from shotplan.pipeline import PlanResult


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_case(case: dict[str, Any], result: PlanResult) -> CaseOutcome:
    expected_hard = bool(case.get("expect_hard_pass", True))
    expected_score = float(case.get("expect_min_score", 0.0))
    hard_ok = result.validation.hard_pass == expected_hard
    score_ok = result.validation.score >= expected_score
    return CaseOutcome(
        case_id=case["id"],
        group=case.get("group", "default"),
        description=case.get("description", ""),
        provider=result.trace.provider,
        hard_pass=result.validation.hard_pass,
        score=round(result.validation.score, 4),
        scores={k: round(v, 4) for k, v in result.validation.scores.items()},
        issues=[i.code for i in result.validation.issues],
        hard_issue_count=len(result.validation.hard_issues),
        duration_ms=round(result.trace.total_duration_ms, 2),
        repair_count=result.trace.repair_count,
        expected_hard_pass=expected_hard,
        expected_min_score=expected_score,
        passed=bool(hard_ok and score_ok),
        error="",
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
            "hard_pass_rate": round(sum(i.hard_pass for i in items) / len(items), 4),
            "avg_score": round(sum(i.score for i in items) / len(items), 4),
            "passed": sum(i.passed for i in items),
        }

    return {
        "case_count": total,
        "hard_pass_rate": round(sum(o.hard_pass for o in outcomes) / total, 4),
        "cases_passed": sum(o.passed for o in outcomes),
        "cases_failed": total - sum(o.passed for o in outcomes),
        "avg_score": round(sum(o.score for o in outcomes) / total, 4),
        "min_score": round(min(o.score for o in outcomes), 4),
        "avg_duration_ms": round(sum(o.duration_ms for o in outcomes) / total, 2),
        "total_repairs": sum(o.repair_count for o in outcomes),
        "dimension_averages": {
            k: round(sum(v) / len(v), 4) for k, v in sorted(dimensions.items())
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
