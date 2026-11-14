"""Soft-issue cases: feasible and solvable, but aimed at the light rule.

Unlike the first red-team set, these do not structurally force a soft failure:
the planner chooses its own start time, so a case only exposes LIGHT_MISUSE if
the model follows the brief's "morning only / no evening" preference. That is
exactly why a small paid probe is needed before a full run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evals.run_eval import load_cases

from shotplan.agents import MockProvider
from shotplan.domain.feasibility import check_feasibility
from shotplan.domain.models import PlanRequest
from shotplan.knowledge.tools import get_tools
from shotplan.pipeline import run_plan

SOFT = Path(__file__).resolve().parents[1] / "evals" / "redteam_soft"


@pytest.fixture(scope="module")
def cases():
    return load_cases(SOFT)


def test_twelve_cases_all_labelled_feasible(cases):
    assert len(cases) == 12
    tools = get_tools()
    for case in cases:
        assert not case.get("expect_infeasible"), f"{case['id']} is labelled infeasible"
        report = check_feasibility(PlanRequest(**case["request"]), tools)
        assert report.feasible, f"{case['id']} is rejected by preflight"


def test_all_mention_a_light_or_time_consideration(cases):
    for case in cases:
        text = case["description"] + " " + " ".join(case["request"].get("preferences", []))
        assert any(word in text for word in ("上午", "中午", "正午", "傍晚", "夜景", "黄金", "晨光", "室内", "白天"))


def test_a_competent_planner_can_pass_them_all(cases):
    """Hard constraints must still be satisfiable; this is a soft-issue test."""
    for case in cases:
        result = run_plan(PlanRequest(**case["request"]), provider=MockProvider("good"))
        assert result.validation.hard_pass, f"{case['id']} is impossible even for the good planner"
