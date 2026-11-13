"""Simulated cost/latency must flow through the pipeline without pretending to be real."""

from __future__ import annotations

import time

from evals.cost_latency import build_report
from evals.simulated_provider import PriceCard, SimulatedProvider
from scripts.build_showcase import cost_block

from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan


def _row() -> dict:
    return {
        "model": "test-model", "price_in_per_1k": 0.001, "price_out_per_1k": 0.002,
        "configured_latency_ms": 800, "provider_calls": 69,
        "total_tokens_in": 100, "total_tokens_out": 200,
        "total_cost_cny": 0.5, "cost_per_call_cny": 0.0072,
        "p50_duration_ms": 812.91, "p95_duration_ms": 1200.87,
        "hard_pass_rate": 1.0,
    }


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


def test_cost_report_latency_is_the_configured_value():
    """Measured p50/p95 carry local overhead and must not reach committed docs.

    Regression: round(1200.6) -> 1201 made the committed showcase page drift
    and the "showcase is up to date" CI gate fail on some runs only.
    """
    document = build_report([_row()], 80)
    assert "800 / 800 ms" in document
    assert "812.91" not in document
    assert "1200.87" not in document
    noisy = dict(_row(), p50_duration_ms=1200.6, p95_duration_ms=2400.9)
    assert build_report([noisy], 80) == document


def test_showcase_cost_block_ignores_measured_latency():
    card = _row()
    first = cost_block({"cards": [card]})
    noisy = cost_block({
        "cards": [dict(card, p50_duration_ms=1300.9, p95_duration_ms=1400.9)]
    })
    assert first == noisy
    assert "800 / 800 ms" in first
    assert "1301" not in first
