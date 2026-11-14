"""The red-team set must be hard, fair, and correctly labelled.

A case that is impossible wastes money and tells you nothing; a case that the
naive planner already passes is not red-teaming. Both ends are checked here,
offline, before any paid run.
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

REDTEAM = Path(__file__).resolve().parents[1] / "evals" / "redteam"


@pytest.fixture(scope="module")
def cases():
    return load_cases(REDTEAM)


def test_the_set_is_the_size_it_claims(cases):
    assert len(cases) == 30
    assert len({case["id"] for case in cases}) == 30


def test_every_case_is_well_formed(cases):
    cities = set(get_tools().city_ids())
    for case in cases:
        request = case["request"]
        if not case.get("expect_infeasible"):
            assert request["city"] in cities, f"{case['id']} uses an unknown city"
        assert isinstance(request["hours"], (int, float))
        assert isinstance(request["budget_cny"], (int, float))
        assert isinstance(request["styles"], list)
        assert case["expect_min_score"] >= 0


def test_infeasible_expectations_match_the_preflight(cases):
    tools = get_tools()
    for case in cases:
        report = check_feasibility(PlanRequest(**case["request"]), tools)
        expected = bool(case.get("expect_infeasible", False))
        assert report.feasible is not expected, (
            f"{case['id']}: preflight says feasible={report.feasible}, "
            f"case expects infeasible={expected}"
        )


def test_naive_planner_fails_most_feasible_cases(cases):
    """If the worst planner passes them, they are not red-teaming anything."""
    feasible = [c for c in cases if not c.get("expect_infeasible")]
    failed = 0
    for case in feasible:
        result = run_plan(PlanRequest(**case["request"]), provider=MockProvider("naive"))
        if not result.validation.hard_pass:
            failed += 1
    assert failed / len(feasible) >= 0.7, f"only {failed}/{len(feasible)} red-team cases are hard"


def test_a_competent_planner_can_still_pass_them(cases):
    """Hard is fine; impossible is not."""
    feasible = [c for c in cases if not c.get("expect_infeasible")]
    passed = 0
    for case in feasible:
        result = run_plan(PlanRequest(**case["request"]), provider=MockProvider("good"))
        if result.validation.hard_pass:
            passed += 1
    assert passed / len(feasible) >= 0.85, (
        f"only {passed}/{len(feasible)} are solvable — the set asks for the impossible"
    )
