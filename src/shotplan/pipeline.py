"""The orchestration layer: preflight -> retrieve -> generate -> parse -> validate -> repair.

Two decisions are deliberately NOT made by a model: whether the brief is
satisfiable at all (preflight), and whether an output is executable (validator).
The validator's report is fed back as a repair prompt when a draft fails. This
is what makes quality measurable and regression-testable instead of opinion.
"""

from __future__ import annotations

import time
from typing import Any

from .agents import MockProvider, parse_plan, validate
from .agents.base import PlanProvider
from .agents.prompts import build_repair_hint
from .config import Settings
from .domain.errors import ShotplanError
from .domain.feasibility import check_feasibility
from .domain.models import (
    Plan,
    PlanRequest,
    PlanResult,
    ValidationIssue,
    ValidationReport,
)
from .knowledge.tools import KnowledgeTools, get_tools
from .storage import RunStore
from .tracing import TraceCollector

__all__ = ["PlanResult", "run_plan"]


def _infeasible_result(
    request: PlanRequest,
    kb: KnowledgeTools,
    agent: PlanProvider,
    collector: TraceCollector,
    started: float,
) -> PlanResult:
    """Build a structured refusal instead of a plan that is bound to fail."""
    feasibility = check_feasibility(request, kb)
    collector.record(
        "preflight", started,
        outcome="infeasible",
        reasons=[reason.code for reason in feasibility.reasons],
    )
    trace = collector.finish(success=False)
    report = ValidationReport(
        issues=[
            ValidationIssue(
                code="INFEASIBLE",
                severity="hard",
                message=f"需求不可满足：{feasibility.headline}",
                expected="一份可执行且满足全部硬约束的方案",
                suggestion=feasibility.reasons[0].suggestion,
            )
        ],
        scores={},
        score=0.0,
        hard_pass=False,
    )
    plan = Plan(
        request_id=request.request_id,
        city=request.city,
        date=request.date,
        provider=agent.name,
        prompt_version=agent.prompt_version,
    )
    return PlanResult(
        request=request,
        plan=plan,
        validation=report,
        trace=trace,
        status="infeasible",
        feasibility=feasibility.to_dict(),
    )


def run_plan(
    request: PlanRequest,
    *,
    provider: PlanProvider | None = None,
    tools: KnowledgeTools | None = None,
    store: RunStore | None = None,
    settings: Settings | None = None,
    max_repairs: int | None = None,
) -> PlanResult:
    """Run the whole pipeline once and return a fully traceable result."""
    cfg = settings or Settings.from_env()
    kb = tools or get_tools()
    agent = provider or MockProvider(mode="good")
    req = request.normalized()
    retries = cfg.max_repairs if max_repairs is None else max(0, max_repairs)
    collector = TraceCollector(agent.name, agent.prompt_version)

    # 0. Preflight feasibility ---------------------------------------------------
    tick = time.perf_counter()
    feasibility = check_feasibility(req, kb)
    if not feasibility.feasible:
        result = _infeasible_result(req, kb, agent, collector, tick)
        if store is not None:
            store.save(result)
        return result
    collector.record("preflight", tick, outcome="feasible")

    # 1. Retrieval ---------------------------------------------------------------
    tick = time.perf_counter()
    context = kb.context_for(req)
    collector.record(
        "retrieve", tick,
        city=context["city"],
        candidates=len(context["candidate_locations"]),
    )

    # 2. Generation --------------------------------------------------------------
    tick = time.perf_counter()
    generation = agent.generate(req, context)
    collector.record(
        "generate", tick,
        tokens_in=generation.tokens_in,
        tokens_out=generation.tokens_out,
        cost_cny=generation.cost_cny,
        model=generation.model,
    )

    # 3. Parsing -----------------------------------------------------------------
    tick = time.perf_counter()
    plan = parse_plan(generation.payload, req, agent.name, agent.prompt_version)
    collector.record("parse", tick, slots=len(plan.slots))

    # 4. Validation --------------------------------------------------------------
    tick = time.perf_counter()
    report = validate(plan, req, kb)
    collector.record(
        "validate", tick,
        score=round(report.score, 3),
        hard_issues=len(report.hard_issues),
        soft_issues=len(report.soft_issues),
    )

    # 5. Repair loop -------------------------------------------------------------
    repairs = 0
    while not report.hard_pass and repairs < retries:
        repairs += 1
        hint = build_repair_hint(report)
        tick = time.perf_counter()
        try:
            fixed = agent.generate(req, context, repair_hint=hint)
            candidate = parse_plan(fixed.payload, req, agent.name, agent.prompt_version)
            candidate_report = validate(candidate, req, kb)
        except ShotplanError as exc:
            collector.record(f"repair_{repairs}", tick, outcome="error", reason=str(exc)[:120])
            break
        # Never accept a regression: keep the better of the two drafts.
        accepted = (candidate_report.hard_pass, candidate_report.score) > (
            report.hard_pass, report.score
        )
        collector.record(
            f"repair_{repairs}", tick,
            tokens_in=fixed.tokens_in,
            tokens_out=fixed.tokens_out,
            cost_cny=fixed.cost_cny,
            hard_issues_before=len(report.hard_issues),
            hard_issues_after=len(candidate_report.hard_issues),
            accepted=accepted,
        )
        if accepted:
            plan, report = candidate, candidate_report
        if report.hard_pass:
            break

    trace = collector.finish(success=report.hard_pass, repair_count=repairs)
    result = PlanResult(request=req, plan=plan, validation=report, trace=trace)
    if store is not None:
        store.save(result)
    return result


def as_summary(result: PlanResult) -> dict[str, Any]:
    """Compact shape used by the API list endpoints and the eval harness."""
    return {
        "run_id": result.trace.run_id,
        "status": result.status,
        "score": round(result.validation.score, 4),
        "hard_pass": result.validation.hard_pass,
        "issues": [i.code for i in result.validation.issues],
        "slots": len(result.plan.slots),
        "total_cost_cny": result.plan.total_cost_cny,
        "duration_ms": round(result.trace.total_duration_ms, 2),
        "provider": result.trace.provider,
    }


__all__ = ["PlanResult", "Plan", "PlanRequest", "ValidationReport", "run_plan", "as_summary"]
