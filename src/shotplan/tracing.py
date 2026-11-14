"""Lightweight tracing: what happened, how long it took, what it cost.

A full observability stack would be overkill here. What matters for an AI
application is that every run is reproducible and that latency/cost are
visible, not hidden inside a client library.
"""

from __future__ import annotations

import time
from typing import Any

from .domain.constraints import rules_fingerprint
from .domain.models import RunTrace, TraceEvent
from .knowledge.loader import knowledge_version


class TraceCollector:
    def __init__(self, provider: str, prompt_version: str) -> None:
        self.trace = RunTrace(provider=provider, prompt_version=prompt_version)
        self.trace.kb_version = knowledge_version()
        self.trace.rules_version = rules_fingerprint()
        self._started = time.perf_counter()

    def set_effective_prompt(self, version: str) -> None:
        """Record the prompt that actually produced the plan, not the default."""
        self.trace.prompt_version_effective = version

    def set_config(self, **values: Any) -> None:
        """Record the request-level settings that shaped this run."""
        self.trace.config = {key: value for key, value in values.items() if value is not None}

    def record(
        self,
        name: str,
        started: float,
        *,
        duration_ms: float | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_cny: float = 0.0,
        **detail: Any,
    ) -> None:
        # Providers measure their own round-trip; prefer that over the
        # orchestrator's timer when it is available (network, retries, simulated latency).
        duration = duration_ms if duration_ms is not None else (time.perf_counter() - started) * 1000
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
        # End-to-end wall clock. The event sum above intentionally keeps the
        # provider-reported durations, which exclude orchestration overhead and
        # the parse/validate work done inside repair rounds.
        self.trace.wall_duration_ms = round(
            (time.perf_counter() - self._started) * 1000, 2
        )
        self.trace.total_cost_cny = sum(e.cost_cny for e in self.trace.events)
        self.trace.repair_count = repair_count
        self.trace.success = success
        return self.trace


def trace_to_markdown(trace: RunTrace) -> str:
    versions = f"kb={trace.kb_version or 'unknown'} / rules={trace.rules_version or 'unknown'}"
    lines = [
        f"### Run `{trace.run_id}`",
        "",
        f"- provider: `{trace.provider}` / `{trace.prompt_version}` / {versions}",
        f"- duration: {trace.wall_duration_ms or trace.total_duration_ms:.1f} ms",
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
