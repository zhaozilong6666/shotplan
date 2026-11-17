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

import logging
import time
from dataclasses import dataclass
from typing import Any

from .agents import MockProvider, build_provider, parse_plan, validate
from .agents.base import GenerationResult, PlanProvider
from .agents.prompts import build_parse_repair_hint, build_repair_hint
from .config import Settings, get_settings
from .domain.errors import (
    ConfigError,
    DeadlineExceeded,
    PipelineError,
    ShotplanError,
    StorageError,
)
from .domain.feasibility import FeasibilityReport, check_feasibility
from .domain.models import (
    Plan,
    PlanRequest,
    PlanResult,
    ValidationIssue,
    ValidationReport,
)
from .grounding import sanitize_source_refs
from .knowledge.tools import KnowledgeTools, get_tools
from .light import align_light_window
from .storage import RunSink
from .timeline import repair_short_shoots
from .tracing import TraceCollector


def _validate_with_light(
    plan: Plan, req: PlanRequest, kb: KnowledgeTools
) -> tuple[Plan, ValidationReport, bool]:
    """Validate, then let code (not the prompt) place the golden-hour shoot.

    Full real-model runs put LIGHT_MISUSE at 31 of 46 issues: the model writes
    plausible prose about light and then schedules the shoot at 11:00. The
    aligner moves the trailing outdoor block into the window, but the aligned
    plan is kept only when it ranks at least as well -- a light fix must never
    buy a time or budget violation.
    """
    report = validate(plan, req, kb)
    if not any(issue.code == "LIGHT_MISUSE" for issue in report.issues):
        return plan, report, False
    aligned = align_light_window(plan, req, kb)
    if aligned is None:
        return plan, report, False
    aligned_report = validate(aligned, req, kb)
    if (aligned_report.hard_pass, aligned_report.score) < (report.hard_pass, report.score):
        return plan, report, False
    return aligned, aligned_report, True


def _validate_and_repair(
    plan: Plan, req: PlanRequest, kb: KnowledgeTools
) -> tuple[Plan, ValidationReport, bool, int]:
    """Validate, then apply the two repairs code can do better than a prompt.

    Both are kept only when the plan does not get worse: a nicer block length is
    not worth a time or budget violation. Dropped citations are returned too:
    silently deleting a model's source list is itself a data change, so the
    caller records the count in the trace instead of hiding it.
    """
    # Fabricated citations are dropped before anything else: one invented
    # pointer should not fail an otherwise deliverable plan.
    plan, dropped_refs = sanitize_source_refs(plan, kb)
    plan, report, light_aligned = _validate_with_light(plan, req, kb)
    if not any(issue.code == "SHOOT_TOO_SHORT" for issue in report.issues):
        return plan, report, light_aligned, dropped_refs
    fixed = repair_short_shoots(plan, req)
    if fixed is None:
        return plan, report, light_aligned, dropped_refs
    fixed_report = validate(fixed, req, kb)
    if (fixed_report.hard_pass, fixed_report.score) < (report.hard_pass, report.score):
        return plan, report, light_aligned, dropped_refs
    return fixed, fixed_report, light_aligned, dropped_refs

logger = logging.getLogger(__name__)


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
        notes=(
            "可恢复的格式问题会被修复并记入 trace.notes；字段级错误（缺 location_id、"
            "自造 activity）会先消耗一次修复预算（repair_parse，最多一次），仍失败才 error；"
            "无法读出的数字直接 ParseError。"
        ),
    ),
    Stage(
        name="validate",
        title="硬约束校验（不经过模型）",
        inputs={"plan": "Plan", "request": "PlanRequest"},
        outputs={"report": "{issues: [...], scores: {...}, score: float, hard_pass: bool}"},
        on_success="ok",
        on_failure="repair",
        notes="15 条规则（10 硬 + 5 软）。hard_pass=True 时直接终止，不进入修复。",
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
    feasibility: FeasibilityReport,
    agent: PlanProvider,
    collector: TraceCollector,
    started: float,
) -> PlanResult:
    """Build a structured refusal instead of a plan that is bound to fail."""
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


def _safe_save(store: RunSink | None, result: PlanResult) -> None:
    """Persist run history without letting a write failure veto a valid plan.

    Storage is an auxiliary capability: if the disk is full or the DB is
    locked, the caller should still receive the plan and a visible warning,
    not a 5xx (or, worse, a 422 for an input error).
    """
    if store is None:
        return
    try:
        store.save(result)
    # OSError matters as much as StorageError: a full disk (ENOSPC) or a
    # permission change can surface before the SQLite layer wraps it. Letting
    # it escape would turn a valid plan into status=error, which is exactly
    # what this function promises not to do.
    except (StorageError, OSError) as exc:
        logger.warning("run history write failed for %s: %s", result.trace.run_id, exc)
        result.validation.issues.append(
            ValidationIssue(
                code="PERSISTENCE_FAILED",
                severity="soft",
                message=f"运行历史写入失败：{exc}",
                expected="本次运行可在 /api/runs 中查询",
                suggestion="检查 SHOTPLAN_DB_PATH、磁盘空间与写权限；结果本身仍然有效",
            )
        )


def run_plan(
    request: PlanRequest,
    *,
    provider: PlanProvider | None = None,
    tools: KnowledgeTools | None = None,
    store: RunSink | None = None,
    settings: Settings | None = None,
    max_repairs: int | None = None,
    request_id: str | None = None,
) -> PlanResult:
    """Public entry point. One top-level error boundary for the whole pipeline.

    Stages such as preflight, knowledge retrieval and validation can raise
    outside the generation/parse/repair guards. Without this boundary those
    exceptions escape to the API as a 500, contradicting `_error_result`'s
    "must not surface as a 500" contract. Every failure becomes a traceable
    `status=error` result instead.
    """
    try:
        return _run_plan(
            request,
            provider=provider,
            tools=tools,
            store=store,
            settings=settings,
            max_repairs=max_repairs,
            request_id=request_id,
        )
    except ConfigError:
        # A misconfigured caller (including a provider built from a different
        # Settings object) is a programming error, not a request-level failure.
        # Swallowing it here would hide exactly the drift this guard detects.
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate last-resort boundary
        if not isinstance(exc, ShotplanError):
            exc = PipelineError(f"{type(exc).__name__}: {exc}")
        try:
            fallback = provider or MockProvider(mode="good")
        except Exception:  # pragma: no cover - provider construction itself failed
            fallback = MockProvider(mode="good")
        collector = TraceCollector(fallback.name, fallback.prompt_version)
        result = _error_result(request.normalized(), fallback, collector, time.perf_counter(), exc)
        _safe_save(store, result)
        return result


def _run_plan(
    request: PlanRequest,
    *,
    provider: PlanProvider | None = None,
    tools: KnowledgeTools | None = None,
    store: RunSink | None = None,
    settings: Settings | None = None,
    max_repairs: int | None = None,
    request_id: str | None = None,
) -> PlanResult:
    """Run the whole pipeline once and return a fully traceable result.

    ``request_id`` is the HTTP request's id, when there is one. It is stored in
    the trace's config snapshot so a historical run can be matched to the
    access-log line that produced it. Offline callers (CLI, evals) omit it,
    which keeps their traces deterministic.
    """
    # Single source of truth for configuration. `Settings.from_env()` here
    # used to skip .env entirely, so offline callers silently ran with code
    # defaults while CLI/API used the developer's real settings.
    cfg = settings or get_settings()
    kb = tools or get_tools()
    agent = provider or build_provider(cfg)
    # Architectural invariant: a real provider must be built from the SAME
    # Settings object that drives the pipeline. Otherwise trace.config records
    # one configuration while the HTTP request uses another, and no amount of
    # post-hoc analysis can explain the run.
    provider_settings = getattr(agent, "settings", None)
    if isinstance(provider_settings, Settings) and provider_settings is not cfg:
        raise ConfigError(
            "provider was built from a different Settings object than run_plan(); "
            "pass the same cfg to build_provider(settings) and run_plan(settings=...)"
        )
    req = request.normalized()
    retries = cfg.max_repairs if max_repairs is None else max(0, max_repairs)
    collector = TraceCollector(agent.name, agent.prompt_version)
    collector.set_config(
        max_repairs=retries,
        pipeline_budget_s=cfg.pipeline_budget_s,
        llm_model=None if cfg.provider == "mock" else cfg.llm_model,
        llm_max_tokens=None if cfg.provider == "mock" else cfg.llm_max_tokens,
        llm_temperature=None if cfg.provider == "mock" else cfg.llm_temperature,
        llm_reasoning_effort=None if cfg.provider == "mock" else cfg.llm_reasoning_effort,
        persist_mode=cfg.persist_mode,
        request_id=request_id,
    )

    # Wall clock, not just a counter: one attempt can legally take the whole
    # provider timeout, so `max_repairs` alone does not bound latency at all.
    budget_s = cfg.pipeline_budget_s
    deadline = time.perf_counter() + budget_s if budget_s > 0 else None

    def expired() -> bool:
        return deadline is not None and time.perf_counter() > deadline

    def call_generate(hint: str | None = None) -> GenerationResult:
        """Only providers that declare support receive the absolute deadline.

        Changing the PlanProvider signature would break every offline planner
        and test double. This opt-in keeps the contract stable while letting
        the real HTTP provider bound its ladder x retries x timeout product.
        """
        kwargs: dict[str, Any] = {}
        if hint is not None:
            kwargs["repair_hint"] = hint
        if deadline is not None and getattr(agent, "accepts_deadline", False):
            kwargs["deadline"] = deadline
        return agent.generate(req, context, **kwargs)

    # 0. Preflight feasibility ---------------------------------------------------
    tick = time.perf_counter()
    feasibility = check_feasibility(req, kb)
    if not feasibility.feasible:
        result = _infeasible_result(req, feasibility, agent, collector, tick)
        _safe_save(store, result)
        return result
    collector.record("preflight", tick, outcome="feasible")
    if expired():
        result = _error_result(
            req, agent, collector, tick,
            DeadlineExceeded(f"在 {budget_s:g}s 预算内未完成预检"),
        )
        _safe_save(store, result)
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
        generation = call_generate()
    except ShotplanError as exc:
        result = _error_result(req, agent, collector, tick, exc)
        _safe_save(store, result)
        return result
    effective_prompt = generation.prompt_version or agent.prompt_version
    collector.set_effective_prompt(effective_prompt)
    collector.record(
        "generate", tick,
        duration_ms=generation.duration_ms,
        tokens_in=generation.tokens_in,
        tokens_out=generation.tokens_out,
        cost_cny=generation.cost_cny,
        model=generation.model,
        prompt_version=effective_prompt,
    )
    # A provider that does not support deadlines can still overrun; do not
    # spend the remaining budget parsing a plan that can never be delivered.
    if expired():
        result = _error_result(
            req, agent, collector, tick,
            DeadlineExceeded(f"在 {budget_s:g}s 预算内未完成生成"),
        )
        _safe_save(store, result)
        return result

    # 3. Parsing -----------------------------------------------------------------
    # Parsing is a trust boundary, so how the payload was recovered is part of
    # the evidence: a model that needed three salvage steps today will need
    # four tomorrow, and that trend must be visible before it becomes a 500.
    tick = time.perf_counter()
    parse_notes: list[str] = []
    parse_error: ShotplanError | None = None
    try:
        plan = parse_plan(
            generation.payload, req, agent.name, effective_prompt, notes=parse_notes
        )
    except ShotplanError as exc:
        parse_error = exc

    # Repairs used so far. A parse failure consumes one: a field-level mistake
    # is usually the cheapest thing to fix and the most expensive thing to
    # discard. Before this, an absent `location_id` turned a nearly usable plan
    # into a hard error while the same budget happily chased a budget overrun.
    repairs = 0
    if parse_error is not None:
        collector.record("parse", tick, outcome="error", reason=str(parse_error)[:160])
        if retries <= 0 or expired():
            result = _error_result(req, agent, collector, tick, parse_error)
            _safe_save(store, result)
            return result
        repairs = 1
        tick = time.perf_counter()
        try:
            fixed = call_generate(build_parse_repair_hint(parse_error))
            plan = parse_plan(
                fixed.payload, req, agent.name, effective_prompt, notes=parse_notes
            )
            collector.set_effective_prompt(fixed.prompt_version or effective_prompt)
            collector.record(
                "repair_parse", tick,
                duration_ms=fixed.duration_ms,
                tokens_in=fixed.tokens_in,
                tokens_out=fixed.tokens_out,
                cost_cny=fixed.cost_cny,
                accepted=True,
            )
        except ShotplanError as exc:
            # One attempt only: a second malformed payload means the schema in
            # the prompt is wrong, which is a code change, not a retry.
            collector.record("repair_parse", tick, outcome="error", reason=str(exc)[:160])
            result = _error_result(req, agent, collector, tick, exc)
            _safe_save(store, result)
            return result
    else:
        collector.record(
            "parse", tick, slots=len(plan.slots), salvage=bool(parse_notes),
            notes=parse_notes[:4],
        )

    # 4. Validation --------------------------------------------------------------
    # The golden-hour placement is decided here, by code, not by the prompt.
    tick = time.perf_counter()
    plan, report, light_aligned, dropped_refs = _validate_and_repair(plan, req, kb)
    collector.record(
        "validate", tick,
        score=round(report.score, 3),
        hard_issues=len(report.hard_issues),
        soft_issues=len(report.soft_issues),
        light_aligned=light_aligned,
        dropped_source_refs=dropped_refs,
    )

    # 5. Repair loop -------------------------------------------------------------
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
            fixed = call_generate(hint)
            candidate = parse_plan(fixed.payload, req, agent.name, effective_prompt)
            candidate, candidate_report, _, candidate_dropped = _validate_and_repair(
                candidate, req, kb
            )
        except ShotplanError as exc:
            collector.record(f"repair_{repairs}", tick, outcome="error", reason=str(exc)[:120])
            # Previously this broke silently with status='ok': the caller saw a
            # passing plan and had no idea a repair had failed. Make it visible
            # without turning a provider glitch into a hard failure.
            report.issues.append(
                ValidationIssue(
                    code="REPAIR_FAILED",
                    severity="soft",
                    message=f"修复循环中断，返回修复前方案：{type(exc).__name__}: {str(exc)[:120]}",
                    expected="修复成功，或明确标注降级",
                    suggestion="检查 provider 输出格式与修复预算；本次结果未经过该轮修复",
                )
            )
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
            # Codes, not just counts: "the repair loop fixed 3 hard issues" says
            # nothing about whether it can fix a budget overrun or only a typo.
            before_codes=[issue.code for issue in report.hard_issues][:6],
            after_codes=[issue.code for issue in candidate_report.hard_issues][:6],
            dropped_source_refs=candidate_dropped,
            accepted=accepted,
        )
        if accepted:
            plan, report = candidate, candidate_report
            # The accepted draft, not the last attempted one, defines provenance.
            collector.set_effective_prompt(fixed.prompt_version or effective_prompt)
        if report.hard_pass:
            break

    trace = collector.finish(success=report.hard_pass, repair_count=repairs)
    result = PlanResult(request=req, plan=plan, validation=report, trace=trace)
    _safe_save(store, result)
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
        # `total_cost_cny` here is the SHOOTING PLAN's price. The storage and
        # dashboard fields with the same name are LLM token spend. Both names
        # are kept for compatibility, but the unambiguous aliases are primary.
        "plan_cost_cny": result.plan.total_cost_cny,
        "total_cost_cny": result.plan.total_cost_cny,
        "wall_ms": round(
            result.trace.wall_duration_ms or result.trace.total_duration_ms, 2
        ),
        "duration_ms": round(
            result.trace.wall_duration_ms or result.trace.total_duration_ms, 2
        ),
        "provider": result.trace.provider,
    }


__all__ = [
    "Plan", "PlanRequest", "PlanResult", "STAGES", "Stage", "ValidationReport",
    "as_summary", "run_plan",
]
