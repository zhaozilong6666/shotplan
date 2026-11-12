"""The repair loop must be measurable, and must refuse worse drafts."""

from __future__ import annotations

from evals.repair_lab import (
    RegressingProvider,
    RepairableProvider,
    build_report,
    run_arm,
)

from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan


def _trace_decisions(result):
    return [
        e.detail["accepted"]
        for e in result.trace.events
        if e.name.startswith("repair_") and "accepted" in e.detail
    ]


def test_repairable_provider_actually_fixes_the_draft():
    result = run_plan(
        PlanRequest(), provider=RepairableProvider(), max_repairs=1
    )
    assert result.trace.repair_count == 1
    assert result.validation.hard_pass is True
    assert _trace_decisions(result) == [True]


def test_regressing_repair_is_refused_and_score_does_not_drop():
    provider = RegressingProvider()
    first = run_plan(PlanRequest(), provider=provider, max_repairs=0)
    repaired = run_plan(PlanRequest(), provider=provider, max_repairs=1)
    assert first.validation.hard_pass is False  # one hard issue, repair loop runs
    assert repaired.trace.repair_count == 1
    assert _trace_decisions(repaired) == [False]
    assert repaired.validation.score == first.validation.score
    assert repaired.validation.hard_pass is False


def test_naive_repair_never_improves():
    from shotplan.agents import MockProvider

    result = run_plan(PlanRequest(), provider=MockProvider("naive"), max_repairs=1)
    assert result.trace.repair_count == 1
    assert _trace_decisions(result) == [False]


def test_run_arm_produces_measurable_metrics():
    cases = [
        {"id": "X1", "request": {"hours": 6, "budget_cny": 1500}},
        {"id": "X2", "request": {"hours": 3, "budget_cny": 800}},
    ]
    arm = run_arm(cases, "repairable", 1)
    metrics = arm.metrics()
    assert metrics["cases"] == 2
    assert metrics["first_pass"] == 0
    assert metrics["after_pass"] == 2
    assert metrics["one_shot_repair_rate"] == 1.0
    assert metrics["accepted"] == 2
    assert metrics["rejected"] == 0
    assert "details" not in metrics


def test_report_is_renderable():
    cases = [{"id": "X1", "request": {"hours": 6}}]
    arms = [
        run_arm(cases, name, repairs)
        for name in ("naive", "repairable", "regressing")
        for repairs in (0, 1)
    ]
    text = build_report(arms, len(cases))
    assert "修复循环实验报告" in text
    assert "单调保护" in text
    assert "repairable/repairs=1" in text
