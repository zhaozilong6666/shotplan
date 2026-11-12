"""A provider that wraps the offline planner with configurable price and latency.

Why this exists: the cost/latency report and the prompt-version comparison must
be exercised BEFORE a real model is connected, otherwise the first real run is
also the first test of the reporting code. This provider makes the numbers
configurable so the plumbing can be verified offline.

It is a **simulation**, not a measurement: tokens come from the offline
provider's estimate, and latency is a configured value (optionally slept) rather
than an observed network round-trip. Reports must say so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from shotplan.agents import MockProvider
from shotplan.agents.base import GenerationResult
from shotplan.domain.models import PlanRequest


@dataclass(frozen=True)
class PriceCard:
    """A vendor-shaped price card. Prices are CNY per 1K tokens."""

    model: str
    price_in_per_1k: float
    price_out_per_1k: float
    latency_ms: float
    prompt_version: str = "planner-v2"


class SimulatedProvider:
    name = "simulated"

    def __init__(
        self,
        card: PriceCard,
        mode: str = "good",
        *,
        sleep: bool = False,
    ) -> None:
        self.card = card
        self.sleep = sleep
        self.prompt_version = card.prompt_version
        self._base = MockProvider(mode=mode, prompt_version=card.prompt_version)

    def generate(
        self,
        request: PlanRequest,
        context: dict[str, Any],
        repair_hint: str | None = None,
    ) -> GenerationResult:
        started = time.perf_counter()
        base = self._base.generate(request, context, repair_hint)
        cost = (
            base.tokens_in / 1000 * self.card.price_in_per_1k
            + base.tokens_out / 1000 * self.card.price_out_per_1k
        )
        if self.sleep and self.card.latency_ms > 0:
            elapsed_ms = (time.perf_counter() - started) * 1000
            remaining = self.card.latency_ms - elapsed_ms
            if remaining > 0:
                time.sleep(remaining / 1000)
        return GenerationResult(
            payload=base.payload,
            tokens_in=base.tokens_in,
            tokens_out=base.tokens_out,
            cost_cny=round(cost, 6),
            duration_ms=self.card.latency_ms,
            model=self.card.model,
            metadata={
                "price_card": self.card.model,
                "simulated": True,
                "prompt_version": self.prompt_version,
            },
        )
