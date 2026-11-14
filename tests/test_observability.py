"""The dashboard must reflect stored runs, including structured refusals."""

from __future__ import annotations

from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.observability import build_dashboard
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore


def _seed(store: RunStore) -> None:
    store.save(run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0))
    store.save(run_plan(PlanRequest(), provider=MockProvider("naive"), max_repairs=1))
    store.save(run_plan(PlanRequest(budget_cny=100), max_repairs=0))


def test_dashboard_groups_by_provider_and_prompt_version(tmp_path):
    store = RunStore(tmp_path / "dash.db")
    _seed(store)
    data = build_dashboard(store)
    assert data["total_runs"] == 3
    assert data["by_status"] == {"infeasible": 1, "ok": 2}
    assert len(data["groups"]) >= 2
    assert data["overall"]["runs"] == 3
    assert 0 < data["overall"]["avg_score"] < 1
    assert data["overall"]["p95_duration_ms"] >= data["overall"]["p50_duration_ms"]
    for group in data["groups"]:
        assert {"provider", "prompt_version", "runs", "hard_pass_rate",
                "avg_score", "infeasible"} <= set(group)


def test_infeasible_runs_are_visible_in_the_dashboard(tmp_path):
    store = RunStore(tmp_path / "dash.db")
    _seed(store)
    data = build_dashboard(store)
    total_infeasible = sum(g["infeasible"] for g in data["groups"])
    assert total_infeasible == 1


def test_empty_dashboard_is_well_formed(tmp_path):
    data = build_dashboard(RunStore(tmp_path / "empty.db"))
    assert data["total_runs"] == 0
    assert data["groups"] == []
    assert data["overall"]["runs"] == 0


def test_recent_payloads_respects_limit(tmp_path):
    store = RunStore(tmp_path / "dash.db")
    for hours in (4, 5, 6):
        store.save(run_plan(PlanRequest(hours=hours), max_repairs=0))
    assert len(store.recent_payloads(2)) == 2
    assert "status" in store.recent_payloads(1)[0]


def test_errors_and_refusals_do_not_grade_the_model():
    """The eval reports already fixed this; the dashboard must match them."""
    from shotplan.observability import _summarise

    rows = [
        {"status": "ok", "score": 0.9, "hard_pass": 1, "duration_ms": 10.0, "cost_cny": 0.1, "repairs": 0},
        {"status": "ok", "score": 0.5, "hard_pass": 0, "duration_ms": 20.0, "cost_cny": 0.1, "repairs": 1},
        {"status": "error", "score": 0.0, "hard_pass": 0, "duration_ms": 99999.0, "cost_cny": 0.3, "repairs": 0},
        {"status": "infeasible", "score": 0.0, "hard_pass": 0, "duration_ms": 5.0, "cost_cny": 0.0, "repairs": 0},
    ]
    summary = _summarise(rows)
    assert summary["runs"] == 4
    assert summary["ok_runs"] == 2
    assert summary["hard_pass_rate"] == 0.5, "1/2 of ok runs, not 1/4 of all runs"
    assert summary["avg_score"] == 0.7
    assert summary["p95_duration_ms"] <= 20.0, "an outage is not a slow plan"
    assert summary["error_runs"] == 1
    assert summary["infeasible_runs"] == 1
    assert summary["error_rate"] == 0.25
    assert summary["total_cost_cny"] == 0.5, "failed calls were still billed"
