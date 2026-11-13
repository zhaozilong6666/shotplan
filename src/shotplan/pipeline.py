"""The orchestration layer: preflight -> retrieve -> generate -> parse -> validate -> repair.

Two decisions are deliberately NOT made by a model: whether the brief is
satisfiable at all (preflight), and whether an output is executable (validator).
The validator's report is fed back as a repair prompt when a draft fails. This
is what makes quality measurable and regression-testable instead of opinion.

The flow below is written as an explicit stage table (``STAGES``) rather than
implied by the order of statements. Two things depend on that table:

* ``scripts/build_pipeline_doc.py`` renders the diagram and the per-stage
  contracts from it, and CI fails if the checked-in doc drifts;
* a test asserts every trace event emitted at runtime is a declared stage, so
  adding a step without declaring it is caught rather than discovered later.

The loop is bounded twice: by ``max_repairs`` (a count) and by a wall-clock
budget (a deadline). Only the second one actually bounds latency, because a
single attempt can consume the whole provider timeout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .agents import MockProvider, parse_plan, validate
from .agents.base import PlanProvider
from .agents.prompts import build_repair_hint
from .config import Settings
from .domain.errors import DeadlineExceeded, ShotplanError
from .domain.feasibility import check_feasibility
from .domain.models import (
    Plan,
    PlanRequest,
    PlanResult,
    ValidationIssue,
    ValidationReport,
)
from .knowledge.tools import KnowledgeTools, get_tools
from .storage import RunSink
from .tracing import TraceCollector

__all__ = ["PlanResult", "run_plan", "STAGES", "Stage"]


@dataclass(frozen=True)
class Stage:
    """One node of the pipeline, with its contract and its exits.

    ``inputs`` / ``outputs`` are documentation with a type, not a schema: the
    point is that the contract is written next to the code that has to satisfy
    it, and that the diagram is generated from it.
    """

    name: str
    title: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    on_success: str
    on_failure: str
    retryable: bool = False
    notes: str = ""


STAGES: tuple[Stage, ...] = (
    Stage(
        name="preflight",
        title="可行性判定（不经过模型）",
        inputs={"request": "PlanRequest(已归一化)"},
        outputs={"feasibility": "{feasible: bool, headline: str, reasons: [{code, suggestion}]}"},
        on_success="retrieve",
        on_failure="infeasible",
        notes="纯算术与约束可满足性。不可满足时结构化拒绝，不生成注定失败的方案。",
    ),
    Stage(
        name="retrieve",
        title="知识库检索",
        inputs={"request": "PlanRequest"},
        outputs={
            "context": "{city, candidate_locations: [...], indoor_fallback: [...], light_window}"
        },
        on_success="generate",
        on_failure="error",
        notes="确定性检索，无模型参与；上下文大小直接影响 token 成本。",
    ),
    Stage(
        name="generate",
        title="生成草稿",
        inputs={"request": "PlanRequest", "context": "检索结果", "repair_hint": "str | None"},
        outputs={"generation": "{payload, tokens_in, tokens_out, cost_cny, duration_ms}"},
        on_success="parse",
        on_failure="error",
        retryable=True,
        notes="provider 内部对可重试状态码最多尝试 3 次，指数退避 0.5s / 1s。",
    ),
    Stage(
        name="parse",
        title="解析（信任边界）",
        inputs={"payload": "dict | str | bytes", "request": "PlanRequest"},
        outputs={"plan": "Plan(slots 已排序, 数值已校验)"},
        on_success="validate",
        on_failure="error",
        notes="可恢复的格式问题会被修复并记入 trace.notes；无法读出的数字直接 ParseError。",
    ),
    Stage(
        name="validate",
        title="硬约束校验（不经过模型）",
        inputs={"plan": "Plan", "request": "PlanRequest"},
        outputs={"report": "{issues: [...], scores: {...}, score: float, hard_pass: bool}"},
        on_success="ok",
        on_failure="repair",
        notes="12 条硬规则 + 软评分。hard_pass=True 时直接终止，不进入修复。",
    ),
    Stage(
        name="repair",
        title="定向修复（有界）",
        inputs={"report": "上一轮校验报告", "plan": "当前最优草稿"},
        outputs={"plan": "更优草稿或保持原方案", "accepted": "bool"},
        on_success="validate",
        on_failure="error",
        retryable=True,
        notes="只在 (hard_pass, score) 严格变大时接受新草稿；受 max_repairs 与墙钟预算双重约束。",
    ),
)

TERMINALS: dict[str, str] = {
    "ok": "返回方案（hard_pass 可能为 False，已尽力修复）",
    "infeasible": "结构化拒绝：给出原因、缺口、建议与理论下限",
    "error": "结构化失败：status=error + PIPELINE_ERROR/DEADLINE_EXCEEDED，不抛 500",
}



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


def _error_result(
    request: PlanRequest,
    agent: PlanProvider,
    collector: TraceCollector,
    started: float,
    exc: ShotplanError,
) -> PlanResult:
    """A broken provider or unparseable output must not surface as a 500."""
    collector.record(
        "error", started,
        error_type=type(exc).__name__,
        message=str(exc)[:200],
    )
    trace = collector.finish(success=False)
    code = "DEADLINE_EXCEEDED" if isinstance(exc, DeadlineExceeded) else "PIPELINE_ERROR"
    report = ValidationReport(
        issues=[
            ValidationIssue(
                code=code,
                severity="hard",
                message=f"规划流水线失败：{exc}",
                expected="一份可执行且满足全部硬约束的方案",
                suggestion=(
                    "预算耗尽时：降低 SHOTPLAN_MAX_REPAIRS / SHOTPLAN_LLM_TIMEOUT，"
                    "或提高 SHOTPLAN_PIPELINE_BUDGET_S；其余情况请检查 provider 配置与"
                    "模型输出格式，离线可用 mock provider 复现"
                ),
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
        status="error",
    )


def run_plan(
    request: PlanRequest,
    *,
    provider: PlanProvider | None = None,
    tools: KnowledgeTools | None = None,
    store: RunSink | None = None,
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

    # Wall clock, not just a counter: one attempt can legally take the whole
    # provider timeout, so `max_repairs` alone does not bound latency at all.
    budget_s = cfg.pipeline_budget_s
    deadline = time.perf_counter() + budget_s if budget_s > 0 else None

    def expired() -> bool:
        return deadline is not None and time.perf_counter() > deadline

    # 0. Preflight feasibility ---------------------------------------------------
    tick = time.perf_counter()
    feasibility = check_feasibility(req, kb)
    if not feasibility.feasible:
        result = _infeasible_result(req, kb, agent, collector, tick)
        if store is not None:
            store.save(result)
        return result
    collector.record("preflight", tick, outcome="feasible")
    if expired():
        result = _error_result(
            req, agent, collector, tick,
            DeadlineExceeded(f"在 {budget_s:g}s 预算内未完成预检"),
        )
        if store is not None:
            store.save(result)
        return result

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
    try:
        generation = agent.generate(req, context)
    except ShotplanError as exc:
        result = _error_result(req, agent, collector, tick, exc)
        if store is not None:
            store.save(result)
        return result
    collector.record(
        "generate", tick,
        duration_ms=generation.duration_ms,
        tokens_in=generation.tokens_in,
        tokens_out=generation.tokens_out,
        cost_cny=generation.cost_cny,
        model=generation.model,
    )

    # 3. Parsing -----------------------------------------------------------------
    # Parsing is a trust boundary, so how the payload was recovered is part of
    # the evidence: a model that needed three salvage steps today will need
    # four tomorrow, and that trend must be visible before it becomes a 500.
    tick = time.perf_counter()
    parse_notes: list[str] = []
    try:
        plan = parse_plan(
            generation.payload, req, agent.name, agent.prompt_version, notes=parse_notes
        )
    except ShotplanError as exc:
        result = _error_result(req, agent, collector, tick, exc)
        if store is not None:
            store.save(result)
        return result
    collector.record(
        "parse", tick, slots=len(plan.slots), salvage=bool(parse_notes),
        notes=parse_notes[:4],
    )

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
        if expired():
            # Degrade, do not fail: return the best draft we already have and
            # say why it was not improved further. A timeout must not discard
            # work that passes most rules.
            collector.record(
                "budget", time.perf_counter(), duration_ms=0.0,
                exhausted=True, budget_s=budget_s, repairs_done=repairs,
            )
            report.issues.append(
                ValidationIssue(
                    code="REPAIR_BUDGET_EXCEEDED",
                    severity="soft",
                    message=f"修复在 {budget_s:g}s 墙钟预算内未完成，返回当前最优草稿",
                    expected="在预算内完成修复，或明确判定无法修复",
                    suggestion=(
                        "降低 SHOTPLAN_MAX_REPAIRS / SHOTPLAN_LLM_TIMEOUT，"
                        "或提高 SHOTPLAN_PIPELINE_BUDGET_S"
                    ),
                )
            )
            break
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
            duration_ms=fixed.duration_ms,
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
