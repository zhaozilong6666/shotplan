from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import as_summary, run_plan
from shotplan.storage import RunStore


def test_good_pipeline_passes_all_hard_constraints():
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=1)
    assert result.validation.hard_pass is True
    assert result.validation.score > 0.90
    assert result.plan.slots
    assert result.plan.total_cost_cny <= result.request.budget_cny


def test_naive_pipeline_fails_in_the_expected_ways():
    result = run_plan(PlanRequest(), provider=MockProvider("naive"), max_repairs=1)
    found = {i.code for i in result.validation.issues}
    assert result.validation.hard_pass is False
    assert {"TIME_OVERRUN", "TRANSFER_BUFFER", "BUDGET_EXCEEDED",
            "UNKNOWN_LOCATION"} <= found


def test_trace_records_every_stage():
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    names = [e.name for e in result.trace.events]
    assert names[:4] == ["retrieve", "generate", "parse", "validate"]
    assert result.trace.total_duration_ms >= sum(e.duration_ms for e in result.trace.events) - 0.01
    assert result.trace.success is True


def test_repair_never_accepts_a_worse_draft():
    single = run_plan(PlanRequest(), provider=MockProvider("naive"), max_repairs=0)
    repaired = run_plan(PlanRequest(), provider=MockProvider("naive"), max_repairs=2)
    assert repaired.trace.repair_count == 2
    assert repaired.validation.score >= single.validation.score


def test_pipeline_persists_when_store_is_provided(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    result = run_plan(PlanRequest(), provider=MockProvider("good"),
                      store=store, max_repairs=0)
    saved = store.get(result.trace.run_id)
    assert saved is not None
    assert saved["validation"]["hard_pass"] is True


def test_custom_brief_is_honoured():
    brief = PlanRequest(hours=3, budget_cny=800, styles=["song"])
    result = run_plan(brief, provider=MockProvider("good"), max_repairs=0)
    assert result.request.budget_cny == 800
    assert result.plan.total_cost_cny <= 800
    assert result.validation.hard_pass is True


def test_as_summary_is_compact():
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    summary = as_summary(result)
    assert summary["hard_pass"] is True
    assert "score" in summary and "run_id" in summary
    assert "plan" not in summary
