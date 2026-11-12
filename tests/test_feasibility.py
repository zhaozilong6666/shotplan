"""Feasibility is decided before generation and must be structured, not an exception."""

from __future__ import annotations

from shotplan.domain.feasibility import check_feasibility
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore


def reason_codes(report):
    return {reason.code for reason in report.reasons}


def test_standard_brief_is_feasible(tools):
    report = check_feasibility(PlanRequest(), tools)
    assert report.feasible is True
    assert report.reasons == []
    assert report.minimums["min_budget_cny"] == 350
    assert report.minimums["min_hours"] == 1.5


def test_budget_below_minimum_is_refused(tools):
    report = check_feasibility(PlanRequest(budget_cny=100), tools)
    assert report.feasible is False
    assert "BUDGET_BELOW_MINIMUM" in reason_codes(report)
    assert "250" in report.reasons[0].shortfall
    assert report.reasons[0].suggestion


def test_time_below_minimum_is_refused(tools):
    report = check_feasibility(PlanRequest(hours=1.0), tools)
    assert "TIME_BELOW_MINIMUM" in reason_codes(report)


def test_unsupported_city_is_refused(tools):
    report = check_feasibility(PlanRequest(city="beijing"), tools)
    assert "UNSUPPORTED_CITY" in reason_codes(report)
    assert "xian" in report.reasons[0].shortfall


def test_conflicting_constraints_are_refused(tools):
    brief = PlanRequest(must_include=["city_wall"], avoid=["city_wall"])
    assert "CONFLICTING_CONSTRAINTS" in reason_codes(check_feasibility(brief, tools))


def test_all_reasons_are_reported_at_once(tools):
    report = check_feasibility(PlanRequest(hours=1.0, budget_cny=0), tools)
    assert {"BUDGET_BELOW_MINIMUM", "TIME_BELOW_MINIMUM"} <= reason_codes(report)
    assert len(report.reasons) == 2


def test_boundary_values_are_still_feasible(tools):
    brief = PlanRequest(hours=1.5, budget_cny=350)
    assert check_feasibility(brief, tools).feasible is True


class _ExplodingProvider:
    """Any call here means the pipeline generated a plan it should have refused."""

    name = "exploding"
    prompt_version = "test"

    def generate(self, request, context, repair_hint=None):
        raise AssertionError("provider must not be called for an infeasible brief")


def test_infeasible_pipeline_never_calls_the_provider(tools):
    result = run_plan(
        PlanRequest(hours=1.0, budget_cny=0),
        provider=_ExplodingProvider(),
        tools=tools,
        max_repairs=0,
    )
    assert result.status == "infeasible"
    assert result.plan.slots == []
    assert result.validation.issues[0].code == "INFEASIBLE"
    assert result.validation.hard_pass is False
    assert [event.name for event in result.trace.events] == ["preflight"]
    assert result.trace.success is False
    assert result.feasibility["feasible"] is False
    assert len(result.feasibility["reasons"]) == 2


def test_infeasible_result_serialises_and_persists(tools, tmp_path):
    store = RunStore(tmp_path / "runs.db")
    result = run_plan(
        PlanRequest(city="beijing"), provider=_ExplodingProvider(),
        tools=tools, store=store, max_repairs=0,
    )
    payload = result.to_dict()
    assert payload["status"] == "infeasible"
    assert payload["feasibility"]["headline"]
    assert store.get(result.trace.run_id)["status"] == "infeasible"


def test_feasible_run_has_ok_status(tools):
    result = run_plan(PlanRequest(), tools=tools, max_repairs=0)
    assert result.status == "ok"
    assert "feasibility" not in result.to_dict()
