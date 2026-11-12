"""Pure domain objects. Standard library only, on purpose.

Why dataclasses instead of pydantic here: the core planning engine must run on a
bare Python install (no network, no pip, no API key). Framework-level schema
validation is added at the API boundary, not in the domain. See docs/adr/0001.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def to_minutes(hhmm: str) -> int:
    """Parse HH:MM into minutes since midnight. Raises ValueError on bad input."""
    if not isinstance(hhmm, str) or ":" not in hhmm:
        raise ValueError(f"invalid time: {hhmm!r}")
    hour, minute = hhmm.split(":", 1)
    h, m = int(hour), int(minute)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid time: {hhmm!r}")
    return h * 60 + m


def fmt_minutes(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


@dataclass
class PlanRequest:
    """A deliberately under-specified brief, like something a real client would send."""

    city: str = "xian"
    date: str = "2026-10-04"
    hours: float = 6.0
    budget_cny: int = 1500
    party_size: int = 1
    styles: list[str] = field(default_factory=lambda: ["tang"])
    preferences: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    request_id: str = field(default_factory=lambda: new_id("req"))

    def normalized(self) -> PlanRequest:
        return PlanRequest(
            city=str(self.city).strip().lower(),
            date=str(self.date).strip(),
            hours=max(1.0, min(float(self.hours), 12.0)),
            budget_cny=max(0, int(self.budget_cny)),
            party_size=max(1, int(self.party_size)),
            styles=[str(s).strip().lower() for s in self.styles if str(s).strip()],
            preferences=[str(p).strip() for p in self.preferences],
            must_include=[str(m).strip().lower() for m in self.must_include],
            avoid=[str(a).strip().lower() for a in self.avoid],
            request_id=self.request_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PlanRequest:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known}).normalized()


@dataclass
class TimeSlot:
    start: str
    end: str
    activity: str  # makeup | travel | shoot | rest | backup
    location_id: str
    label: str = ""
    cost_cny: int = 0
    source_refs: list[str] = field(default_factory=list)
    notes: str = ""

    def minutes(self) -> int:
        return to_minutes(self.end) - to_minutes(self.start)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TimeSlot:
        known = {f for f in cls.__dataclass_fields__}
        values = dict(raw)
        values.setdefault("label", values.get("activity", ""))
        return cls(**{k: v for k, v in values.items() if k in known})


@dataclass
class Plan:
    plan_id: str = field(default_factory=lambda: new_id("plan"))
    request_id: str = ""
    city: str = "xian"
    date: str = ""
    slots: list[TimeSlot] = field(default_factory=list)
    styling: dict[str, Any] = field(default_factory=dict)
    total_cost_cny: int = 0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    provider: str = "mock"
    prompt_version: str = "planner-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Plan:
        return cls(
            plan_id=str(raw.get("plan_id") or new_id("plan")),
            request_id=str(raw.get("request_id", "")),
            city=str(raw.get("city", "xian")),
            date=str(raw.get("date", "")),
            slots=[TimeSlot.from_dict(s) for s in raw.get("slots", []) if isinstance(s, dict)],
            styling=dict(raw.get("styling") or {}),
            total_cost_cny=int(raw.get("total_cost_cny") or 0),
            alternatives=[a for a in raw.get("alternatives", []) if isinstance(a, dict)],
            assumptions=[str(a) for a in raw.get("assumptions", [])],
            source_refs=[str(r) for r in raw.get("source_refs", [])],
            provider=str(raw.get("provider", "unknown")),
            prompt_version=str(raw.get("prompt_version", "unknown")),
        )


@dataclass
class ValidationIssue:
    code: str
    severity: str  # hard | soft
    message: str
    slot_index: int | None = None
    actual: str = ""
    expected: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    hard_pass: bool = True

    @property
    def hard_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "hard"]

    @property
    def soft_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "soft"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "hard_pass": self.hard_pass,
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class TraceEvent:
    step: int
    name: str
    duration_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunTrace:
    run_id: str = field(default_factory=lambda: new_id("run"))
    created_at: str = field(default_factory=utc_now)
    provider: str = "mock"
    prompt_version: str = "planner-v1"
    events: list[TraceEvent] = field(default_factory=list)
    total_duration_ms: float = 0.0
    total_cost_cny: float = 0.0
    repair_count: int = 0
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
            "events": [e.to_dict() for e in self.events],
            "total_duration_ms": round(self.total_duration_ms, 2),
            "total_cost_cny": round(self.total_cost_cny, 6),
            "repair_count": self.repair_count,
            "success": self.success,
        }


@dataclass
class PlanResult:
    request: PlanRequest
    plan: Plan
    validation: ValidationReport
    trace: RunTrace

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "plan": self.plan.to_dict(),
            "validation": self.validation.to_dict(),
            "trace": self.trace.to_dict(),
        }
