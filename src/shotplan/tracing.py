"""Lightweight tracing: what happened, how long it took, what it cost.

A full observability stack would be overkill here. What matters for an AI
application is that every run is reproducible and that latency/cost are
visible, not hidden inside a client library.
"""

from __future__ import annotations

import time
from typing import Any

from .domain.models import RunTrace, TraceEvent


class TraceCollector:
    def __init__(self, provider: str, prompt_version: str) -> None:
        self.trace = RunTrace(provider=provider, prompt_version=prompt_version)
        self._started = time.perf_counter()

    def record(
        self,
        name: str,
        started: float,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_cny: float = 0.0,
        **detail: Any,
    ) -> None:
        duration = (time.perf_counter() - started) * 1000
        self.trace.events.append(
            TraceEvent(
                step=len(self.trace.events) + 1,
                name=name,
                duration_ms=duration,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_cny=cost_cny,
                detail=detail,
            )
        )

    def finish(self, *, success: bool, repair_count: int = 0) -> RunTrace:
        self.trace.total_duration_ms = sum(e.duration_ms for e in self.trace.events)
        self.trace.total_cost_cny = sum(e.cost_cny for e in self.trace.events)
        self.trace.repair_count = repair_count
        self.trace.success = success
        return self.trace


def trace_to_markdown(trace: RunTrace) -> str:
    lines = [
        f"### Run `{trace.run_id}`",
        "",
        f"- provider: `{trace.provider}` / `{trace.prompt_version}`",
        f"- duration: {trace.total_duration_ms:.1f} ms",
        f"- estimated cost: {trace.total_cost_cny:.6f} CNY",
        f"- repairs: {trace.repair_count}",
        "",
        "| step | name | ms | tokens in | tokens out | detail |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for event in trace.events:
        detail = ", ".join(f"{k}={v}" for k, v in event.detail.items()) or "-"
        lines.append(
            f"| {event.step} | {event.name} | {event.duration_ms:.1f} | "
            f"{event.tokens_in} | {event.tokens_out} | {detail} |"
        )
    return "\n".join(lines)
