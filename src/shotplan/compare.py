"""Give the user a choice instead of a single decree.

Until now the product produced exactly one plan. Even a good plan feels dumb
when there is nothing to compare it against: the user cannot tell whether the
system made a trade-off or simply never considered one.

This module runs the SAME brief through three explicit strategies, measures
each result with the real validator, and states what each option gives up.
It does not invent a fourth "best" answer -- the recommendation is derived
from the user's own preference words, with the evidence shown.

All three runs go through the real `run_plan` with the same settings as
`/api/plan` (including the repair policy), so every number here is traceable
and every plan has been through the same checks.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .agents import build_provider
from .config import Settings, get_settings
from .domain.errors import ShotplanError
from .domain.models import PlanRequest, to_minutes
from .knowledge.tools import KnowledgeTools, get_tools

STRATEGIES: tuple[dict[str, str], ...] = (
    {
        "id": "showcase",
        "label": "出片优先",
        "intent": "优先黄金光线与外景数量，在给定时间内多拍",
    },
    {
        "id": "budget",
        "label": "省钱优先",
        "intent": "在给定预算内优先便宜场地，保留完整妆造",
    },
    {
        "id": "easy",
        "label": "轻松优先",
        "intent": "优先近距离场地，减少转场与赶场",
    },
)

PREFERENCE_HINTS: dict[str, tuple[str, ...]] = {
    "budget": ("省钱", "便宜", "控制预算", "预算紧", "性价比", "少花钱"),
    "easy": ("轻松", "别太累", "不赶", "就近", "少走路", "懒", "半天"),
    "showcase": ("出片", "氛围感", "光线", "大片", "多拍", "精致"),
}


def _exclusions(req: PlanRequest, strategy_id: str, tools: KnowledgeTools) -> list[str]:
    """Make the strategies structurally different, not just differently named.

    The offline planner responds to `avoid`, so excluding the most expensive
    stops (budget) or the farthest ones (easy) really changes the candidate
    set. Using `must_include` instead would add a hard rule that a real model
    might violate, turning a comparison into a failure.
    """
    if strategy_id in ("showcase", ""):
        return list(req.avoid)
    # Never exclude the indoor styling studio: it is where makeup happens,
    # and excluding it would push makeup into a scenic spot.
    locations = [
        loc for loc in tools.locations(req.city)
        if loc["id"] not in req.avoid and not loc.get("indoor")
    ]
    if len(locations) < 4:
        return list(req.avoid)
    key = "cost_cny" if strategy_id == "budget" else "travel_min_from_center"
    ranked = sorted(locations, key=lambda loc: (-int(loc.get(key, 0)), loc["id"]))
    excluded = [loc["id"] for loc in ranked[:2]]
    return list(dict.fromkeys([*req.avoid, *excluded]))


def _variant(request: PlanRequest, strategy_id: str, tools: KnowledgeTools) -> PlanRequest:
    """Change the *preferences*, never the user's hard constraints.

    An earlier version quietly stretched `hours` to 7 and shrank `budget_cny` to
    65% so the three cards would look different. That produced plans which did
    not satisfy the brief the user actually typed, while presenting them as
    "your three options" -- a comparison built on a lie. Hours and budget are
    now copied verbatim; only the preference words and the excluded candidates
    differ, which changes what the planner optimises for without changing what
    the user asked for.
    """
    req = request.normalized()
    if strategy_id == "showcase":
        prefs = list(dict.fromkeys([*req.preferences, "黄金光线", "多拍"]))
        return replace(req, preferences=prefs)
    if strategy_id == "budget":
        return replace(
            req,
            avoid=_exclusions(req, "budget", tools),
            preferences=list(dict.fromkeys([*req.preferences, "控制预算", "省钱"])),
        )
    if strategy_id == "easy":
        return replace(
            req,
            avoid=_exclusions(req, "easy", tools),
            preferences=list(dict.fromkeys([*req.preferences, "少转场", "轻松"])),
        )
    return req


def _relaxations(variant: PlanRequest, base: PlanRequest) -> list[str]:
    """Hard facts that became looser than the brief the user actually typed.

    Reducing hours or budget is still within the request, so it is allowed.
    Increasing them, changing city/date/party, dropping a required place,
    dropping an exclusion, or dropping a style is a relaxation and must be
    visible instead of silently presented as "your option".
    """
    out: list[str] = []
    if variant.city != base.city:
        out.append(f"换城市：{base.city} → {variant.city}")
    if variant.date != base.date:
        out.append(f"换日期：{base.date} → {variant.date}")
    if variant.party_size != base.party_size:
        out.append(f"改人数：{base.party_size} → {variant.party_size}")
    if variant.hours > base.hours:
        out.append(f"多留 {variant.hours - base.hours:g} 小时")
    if variant.budget_cny > base.budget_cny:
        out.append(f"多预算 {variant.budget_cny - base.budget_cny} 元")
    missing_required = sorted(set(base.must_include) - set(variant.must_include))
    if missing_required:
        out.append(f"少了必去地点：{', '.join(missing_required)}")
    dropped_avoid = sorted(set(base.avoid) - set(variant.avoid))
    if dropped_avoid:
        out.append(f"去掉了排除项：{', '.join(dropped_avoid)}")
    if set(base.styles) - set(variant.styles):
        out.append("少了风格")
    return out


def _measure(result: Any, tools: KnowledgeTools) -> dict[str, Any]:
    plan, report = result.plan, result.validation
    shoot = [s for s in plan.slots if s.activity == "shoot"]
    travel = [s for s in plan.slots if s.activity == "travel"]
    scene_travel = 0
    for slot in shoot:
        location = tools.location(plan.city, slot.location_id)
        if location:
            scene_travel += int(location.get("travel_min_from_center", 0))
    light_start, light_end = tools.light_window(
        plan.date or result.request.date, plan.city
    )
    window_start, window_end = to_minutes(light_start), to_minutes(light_end)
    light_minutes = sum(
        max(
            0,
            min(to_minutes(s.end), window_end) - max(to_minutes(s.start), window_start),
        )
        for s in shoot
    )
    return {
        "status": result.status,
        "hard_pass": report.hard_pass,
        "score": report.score,
        "scores": dict(report.scores),
        "total_cost_cny": plan.total_cost_cny,
        # LLM token spend for this strategy, so a triple run is not free-by-silence.
        "cost_cny": round(float(getattr(result.trace, "total_cost_cny", 0.0)), 6),
        "slot_count": len(plan.slots),
        "shoot_count": len(shoot),
        "travel_count": len(travel),
        "shoot_minutes": sum(s.minutes() for s in shoot),
        # Minutes of shooting that actually fall inside the solar golden window.
        "light_minutes": light_minutes,
        # Sum of the scenes' own distance from the city centre: this is what
        # "less running around" actually means, not the fixed transfer block.
        "scene_travel_minutes": scene_travel,
        "repair_count": int(getattr(result.trace, "repair_count", 0)),
        "issue_codes": [i.code for i in report.issues][:6],
        "plan_id": plan.plan_id,
        "slots": [
            {
                "start": s.start, "end": s.end, "activity": s.activity,
                "location_id": s.location_id, "label": s.label,
                "cost_cny": s.cost_cny,
            }
            for s in plan.slots
        ],
    }


def _tradeoffs(metrics: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Computed differences, not marketing copy."""
    out: list[str] = []
    delta_cost = metrics["total_cost_cny"] - baseline["total_cost_cny"]
    delta_shoot = metrics["shoot_count"] - baseline["shoot_count"]
    delta_minutes = metrics["shoot_minutes"] - baseline["shoot_minutes"]
    delta_light = metrics.get("light_minutes", 0) - baseline.get("light_minutes", 0)
    delta_score = round(metrics["score"] - baseline["score"], 4)
    if delta_cost:
        out.append(f"{'多花' if delta_cost > 0 else '少花'} {abs(delta_cost)} 元")
    if delta_shoot:
        out.append(f"{'多' if delta_shoot > 0 else '少'} {abs(delta_shoot)} 个拍摄点")
    if delta_minutes:
        out.append(f"拍摄时长{'多' if delta_minutes > 0 else '少'} {abs(delta_minutes)} 分钟")
    if delta_light:
        out.append(
            f"黄金窗口内拍摄{'多' if delta_light > 0 else '少'} {abs(delta_light)} 分钟"
        )
    if delta_score:
        out.append(f"综合得分{'高' if delta_score > 0 else '低'} {abs(delta_score)}")
    if not out:
        out.append("与出片优先版在可量化指标上持平")
    return out


def _recommend(strategies: list[dict[str, Any]], request: PlanRequest) -> dict[str, Any]:
    """Recommend only among options that actually honour the user's brief."""
    text = " ".join(request.preferences)
    evidence: list[str] = []
    compliant = [s for s in strategies if s.get("within_original_constraints", True)]
    viable = [s for s in compliant if s["metrics"]["hard_pass"]]
    pool = viable or compliant or strategies
    chosen = None
    blocked: tuple[str, str] | None = None
    for strategy_id, words in PREFERENCE_HINTS.items():
        hit = next((w for w in words if w in text), None)
        if not hit:
            continue
        if any(s["id"] == strategy_id for s in pool):
            evidence.append(hit)
            chosen = strategy_id
            break
        # The user asked for this direction, but that plan failed hard rules.
        # Say so instead of recommending an unexecutable plan.
        blocked = (strategy_id, hit)
    all_failed = not viable
    if chosen:
        reason = f"你在偏好里提到「{'、'.join(evidence)}」，按这个方向推荐"
        basis = "preference"
        result = {"id": chosen, "reason": reason, "basis": basis, "evidence": evidence}
        if all_failed:
            result["note"] = "本次没有策略通过硬约束；这个推荐只是得分最高的草稿，不应直接执行。"
        return result

    best = max(pool, key=lambda s: s["metrics"]["score"])
    if blocked:
        reason = (
            f"你提到「{blocked[1]}」，但该策略没有通过硬约束；"
            f"已改推荐满足约束且得分最高的「{best['label']}」"
        )
        evidence.append(blocked[1])
        result = {
            "id": best["id"],
            "reason": reason,
            "basis": "constraint_fallback",
            "evidence": evidence,
            "fallback_from": blocked[0],
        }
        if all_failed:
            result["note"] = "本次没有策略通过硬约束；这个推荐只是得分最高的草稿，不应直接执行。"
        return result
    reason = "你没有表达明确倾向，按「先过硬约束、再比综合得分」推荐"
    evidence.append(f"最高得分 {best['metrics']['score']}")
    result = {"id": best["id"], "reason": reason, "basis": "score", "evidence": evidence}
    if all_failed:
        result["note"] = "本次没有策略通过硬约束；这个推荐只是得分最高的草稿，不应直接执行。"
    return result


def compare_strategies(
    request: PlanRequest,
    *,
    settings: Settings | None = None,
    provider: Any = None,
) -> dict[str, Any]:
    """Three real runs, one honest comparison. Never raises on a bad strategy."""
    cfg = settings or get_settings()
    agent = provider or build_provider(cfg)
    tools = get_tools()
    base = request.normalized()
    strategies: list[dict[str, Any]] = []

    for spec in STRATEGIES:
        variant = _variant(base, spec["id"], tools)
        relaxations = _relaxations(variant, base)
        entry: dict[str, Any] = {
            "id": spec["id"],
            "label": spec["label"],
            "intent": spec["intent"],
            "request": variant.to_dict(),
            # Hard constraints must be identical to the brief the user typed.
            "within_original_constraints": not relaxations,
            "relaxations": relaxations,
        }
        try:
            from .pipeline import run_plan

            result = run_plan(
                variant, provider=agent, settings=cfg, max_repairs=cfg.max_repairs
            )
            entry["metrics"] = _measure(result, tools)
        except ShotplanError as exc:
            entry["metrics"] = {
                "status": "error", "hard_pass": False, "score": 0.0,
                "scores": {}, "total_cost_cny": 0, "cost_cny": 0.0,
                "slot_count": 0, "shoot_count": 0, "travel_count": 0,
                "shoot_minutes": 0, "scene_travel_minutes": 0, "repair_count": 0,
                "issue_codes": [type(exc).__name__], "plan_id": "", "slots": [],
            }
            entry["error"] = str(exc)[:200]
        strategies.append(entry)

    baseline = strategies[0]["metrics"]
    for entry in strategies[1:]:
        entry["tradeoffs"] = _tradeoffs(entry["metrics"], baseline)
    strategies[0]["tradeoffs"] = ["作为对照基准：按用户原始时长与预算安排"]

    # A comparison that shows three identical plans is worse than no
    # comparison. Detect it and say so instead of pretending there is a choice.
    def fingerprint(entry: dict[str, Any]) -> tuple:
        return tuple(
            (s["location_id"], s["activity"], s["start"], s["end"])
            for s in entry["metrics"]["slots"]
        )

    groups: dict[tuple, list[str]] = {}
    for entry in strategies:
        groups.setdefault(fingerprint(entry), []).append(entry["id"])
    for entry in strategies:
        same = groups[fingerprint(entry)]
        entry["identical_to"] = [x for x in same if x != entry["id"]]

    recommendation = _recommend(strategies, base)
    distinct = len({fingerprint(e) for e in strategies})
    if distinct < len(strategies):
        if distinct == 1:
            recommendation["note"] = (
                "在你的条件下三种取舍都收敛到同一方案——这说明它是个稳健解，"
                "而不是我们随便挑了一个；愿意放宽时长或预算时才会出现真正的分岔"
            )
        else:
            recommendation["note"] = (
                f"当前条件只产出 {distinct} 种不同方案，其余策略与之重合；"
                "重合的部分说明那个选择对多种目标都成立"
            )
    return {
        "base_request": base.to_dict(),
        "strategies": strategies,
        "distinct_plans": distinct,
        "recommendation": recommendation,
        "provider": agent.name,
    }
