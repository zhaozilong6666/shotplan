"""The flow is a stage table, and the table has to match reality.

Three independent things are checked here:

1. the declared graph is well formed (every transition points somewhere);
2. the *running* code only emits events that the table declares — this is the
   link between documentation and behaviour, not just doc-vs-doc;
3. the wall-clock budget really stops the repair loop, and does it by
   degrading (return the best draft) rather than discarding work.
"""

from __future__ import annotations

import json
import time

from shotplan.agents import MockProvider
from shotplan.agents.base import GenerationResult
from shotplan.config import Settings
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import STAGES, TERMINALS, run_plan

ROOT_MARKER = "shotplan"


def test_every_transition_target_exists():
    stage_names = {stage.name for stage in STAGES}
    allowed = stage_names | set(TERMINALS)
    for stage in STAGES:
        assert stage.on_success in allowed, f"{stage.name}.on_success -> {stage.on_success}"
        assert stage.on_failure in allowed, f"{stage.name}.on_failure -> {stage.on_failure}"


def test_stage_names_are_unique_and_terminal_free():
    names = [stage.name for stage in STAGES]
    assert len(names) == len(set(names))
    assert not set(names) & set(TERMINALS)


def test_every_stage_documents_its_contract():
    for stage in STAGES:
        assert stage.inputs, f"{stage.name} has no documented inputs"
        assert stage.outputs, f"{stage.name} has no documented outputs"
        assert stage.title and stage.notes


def _event_names(result) -> set[str]:
    return {event.name for event in result.trace.events}


def _is_declared(name: str) -> bool:
    return (
        name in {stage.name for stage in STAGES}
        or name in {"budget", "error"}
        or (name.startswith("repair_") and name[7:].isdigit())
    )


def test_runtime_events_are_all_declared():
    """A new step added without declaring it must fail here, not in review."""
    runs = [
        run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=1),
        run_plan(PlanRequest(), provider=MockProvider("naive"), max_repairs=1),
        run_plan(PlanRequest(hours=0.5), provider=MockProvider("good"), max_repairs=1),
    ]

    class Broken:
        name = "broken"
        prompt_version = "v1"

        def generate(self, request, context, repair_hint=None):
            from shotplan.domain.errors import ProviderError

            raise ProviderError("upstream down")

    runs.append(run_plan(PlanRequest(), provider=Broken(), max_repairs=0))

    for result in runs:
        for name in _event_names(result):
            assert _is_declared(name), f"trace emitted undeclared stage {name!r}"


def test_the_infeasible_path_never_generates():
    result = run_plan(PlanRequest(hours=0.5), provider=MockProvider("good"))
    assert result.status == "infeasible"
    assert _event_names(result) == {"preflight"}


def test_a_plain_success_does_not_touch_the_repair_stage():
    result = run_plan(PlanRequest(), provider=MockProvider("good"), max_repairs=3)
    assert result.validation.hard_pass
    assert not any(name.startswith("repair") for name in _event_names(result))
    assert _event_names(result) >= {"preflight", "retrieve", "generate", "parse", "validate"}


class SlowNaive:
    """Always fails hard rules, and takes measurable time doing it."""

    name = "slow-naive"
    prompt_version = "v1"

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def generate(self, request, context, repair_hint=None) -> GenerationResult:
        self.calls += 1
        time.sleep(self.delay)
        inner = MockProvider("naive").generate(request, context, repair_hint)
        return inner


def test_the_budget_stops_the_repair_loop():
    provider = SlowNaive(0.05)
    # 5 repairs x 50ms would need 250ms; the budget allows about two.
    result = run_plan(
        PlanRequest(), provider=provider, max_repairs=5,
        settings=Settings(pipeline_budget_s=0.12),
    )
    assert provider.calls < 6, "the budget did not stop the loop"
    assert "budget" in _event_names(result)
    budget_event = next(e for e in result.trace.events if e.name == "budget")
    assert budget_event.detail["exhausted"] is True


def test_budget_exhaustion_degrades_instead_of_discarding():
    provider = SlowNaive(0.05)
    result = run_plan(
        PlanRequest(), provider=provider, max_repairs=5,
        settings=Settings(pipeline_budget_s=0.12),
    )
    # A draft exists and is returned; only the improvement stopped.
    assert result.status == "ok"
    assert result.plan.slots
    codes = [issue.code for issue in result.validation.issues]
    assert "REPAIR_BUDGET_EXCEEDED" in codes
    assert result.validation.hard_pass is False


def test_budget_exhausted_before_generation_is_a_structured_error():
    result = run_plan(
        PlanRequest(), provider=MockProvider("good"), max_repairs=1,
        settings=Settings(pipeline_budget_s=1e-9),
    )
    assert result.status == "error"
    assert result.validation.issues[0].code == "DEADLINE_EXCEEDED"
    json.dumps(result.to_dict())


def test_budget_zero_disables_the_deadline():
    provider = SlowNaive(0.001)
    result = run_plan(
        PlanRequest(), provider=provider, max_repairs=2,
        settings=Settings(pipeline_budget_s=0.0),
    )
    assert provider.calls == 3, "with the deadline disabled every repair must run"
    assert "budget" not in _event_names(result)


def test_worst_case_composition_matches_the_code():
    from scripts.build_pipeline_doc import worst_case

    numbers = worst_case()
    cfg = Settings()
    assert numbers["max_repairs"] == cfg.max_repairs
    assert numbers["http_calls"] == (1 + cfg.max_repairs) * numbers["max_attempts_per_call"]
    assert numbers["worst_case_s"] > numbers["budget_s"], (
        "if the worst case were below the budget, the deadline would be decorative"
    )


def test_pipeline_doc_is_generated_from_the_table():
    from scripts.build_pipeline_doc import DOC_PATH, build

    assert DOC_PATH.exists(), "docs/pipeline-contract.md is missing"
    assert DOC_PATH.read_text(encoding="utf-8") == build(), (
        "docs/pipeline-contract.md is stale: run "
        "python scripts/build_pipeline_doc.py --write"
    )
