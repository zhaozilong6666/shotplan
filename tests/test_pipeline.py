from shotplan.agents import MockProvider
from shotplan.agents.base import GenerationResult
from shotplan.domain.errors import StorageError
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
    assert names[:5] == ["preflight", "retrieve", "generate", "parse", "validate"]
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


def test_trace_records_what_produced_the_score():
    """Without these versions a historical score cannot be re-interpreted."""
    first = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    second = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    assert len(first.trace.kb_version) == 12
    assert len(first.trace.rules_version) == 12
    assert first.trace.kb_version == second.trace.kb_version
    assert first.trace.rules_version == second.trace.rules_version
    assert first.trace.wall_duration_ms >= first.trace.total_duration_ms
    assert first.trace.config["max_repairs"] == 0
    assert "pipeline_budget_s" in first.trace.config


def test_request_id_is_recorded_only_when_supplied():
    offline = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=0)
    assert "request_id" not in offline.trace.config

    tagged = run_plan(
        PlanRequest(), provider=MockProvider("good"), max_repairs=0,
        request_id="trace-42",
    )
    assert tagged.trace.config["request_id"] == "trace-42"


def test_persistence_failure_does_not_veto_a_valid_plan():
    class BrokenStore:
        def save(self, result):
            raise StorageError("disk full")

    result = run_plan(
        PlanRequest(), provider=MockProvider("good"),
        store=BrokenStore(), max_repairs=0,
    )
    assert result.status == "ok"
    assert result.validation.hard_pass is True
    assert "PERSISTENCE_FAILED" in {i.code for i in result.validation.issues}


def test_parse_repair_hint_matches_the_parser_enum():
    """A repair hint that disagrees with the parser teaches a new failure."""
    from shotplan.agents.planner import _VALID_ACTIVITIES
    from shotplan.agents.prompts import _PARSE_SCHEMA_RULES

    for activity in _VALID_ACTIVITIES:
        assert activity in _PARSE_SCHEMA_RULES
    assert "costume" not in _PARSE_SCHEMA_RULES


class _MalformedThenValid:
    """Emits a schema-invalid payload once, then behaves like the good planner.

    This is the shape of the real failure we saw in production runs: the model
    answered, the JSON was readable, but a slot used an activity outside the
    enum. It used to be an unrecoverable error.
    """

    name = "flaky"
    prompt_version = "flaky-v1"

    def __init__(self, always_bad: bool = False) -> None:
        self.calls = 0
        self.always_bad = always_bad
        self._good = MockProvider("good")

    def generate(self, request, context, repair_hint=None):
        self.calls += 1
        if self.always_bad or self.calls == 1:
            base = self._good.generate(request, context)
            return GenerationResult(
                payload={
                    "slots": [
                        {
                            "start": "09:00",
                            "end": "11:00",
                            "activity": "costume",
                            "label": "妆造",
                            "cost_cny": 300,
                        }
                    ]
                },
                tokens_in=base.tokens_in,
                tokens_out=base.tokens_out,
                cost_cny=0.0,
                duration_ms=0.0,
                model="flaky",
                metadata={},
            )
        return self._good.generate(request, context, repair_hint=repair_hint)


def test_parse_failure_gets_one_bounded_repair():
    provider = _MalformedThenValid()
    result = run_plan(PlanRequest(), provider=provider, max_repairs=1)
    assert provider.calls == 2
    assert result.status == "ok"
    assert result.validation.hard_pass is True
    assert result.trace.repair_count == 1
    assert "repair_parse" in {e.name for e in result.trace.events}


def test_parse_repair_is_skipped_when_budget_is_zero():
    provider = _MalformedThenValid()
    result = run_plan(PlanRequest(), provider=provider, max_repairs=0)
    assert provider.calls == 1
    assert result.status == "error"
    assert any(i.code == "PIPELINE_ERROR" for i in result.validation.issues)


def test_parse_repair_gives_up_after_one_attempt():
    provider = _MalformedThenValid(always_bad=True)
    result = run_plan(PlanRequest(), provider=provider, max_repairs=3)
    assert provider.calls == 2, "malformed output must not burn the whole budget"
    assert result.status == "error"
