"""Simulated cost/latency must flow through the pipeline without pretending to be real."""

from __future__ import annotations

import time

from evals.simulated_provider import PriceCard, SimulatedProvider

from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan


def test_simulated_provider_attaches_cost_and_metadata():
    card = PriceCard("test-model", 0.01, 0.02, 0)
    result = run_plan(
        PlanRequest(), provider=SimulatedProvider(card), max_repairs=0
    )
    generate = next(e for e in result.trace.events if e.name == "generate")
    assert generate.cost_cny > 0
    assert generate.tokens_in > 0 and generate.tokens_out > 0
    assert result.trace.total_cost_cny > 0
    assert generate.detail["model"] == "test-model"


def test_simulated_latency_is_configurable():
    card = PriceCard("slow-model", 0.01, 0.02, 20)
    provider = SimulatedProvider(card, sleep=True)
    started = time.perf_counter()
    result = run_plan(PlanRequest(), provider=provider, max_repairs=0)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms >= 15
    generate = next(e for e in result.trace.events if e.name == "generate")
    assert generate.duration_ms == 20


def test_price_card_is_frozen_and_versioned():
    card = PriceCard("m", 0.001, 0.002, 10, prompt_version="planner-v1")
    provider = SimulatedProvider(card)
    assert provider.prompt_version == "planner-v1"
    result = run_plan(PlanRequest(), provider=provider, max_repairs=0)
    assert result.trace.prompt_version == "planner-v1"
