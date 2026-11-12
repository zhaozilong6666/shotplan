"""Provider contract. Any model backend implements this one method."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..domain.models import PlanRequest


@dataclass
class GenerationResult:
    """Raw model output plus the metadata needed for tracing and cost accounting."""

    payload: dict[str, Any] | str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    duration_ms: float = 0.0
    model: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PlanProvider(Protocol):
    name: str
    prompt_version: str

    def generate(
        self,
        request: PlanRequest,
        context: dict[str, Any],
        repair_hint: str | None = None,
    ) -> GenerationResult:
        """Return a plan-shaped payload. Never raise on a bad plan; the
        parser and validator own the failure semantics."""
        ...
