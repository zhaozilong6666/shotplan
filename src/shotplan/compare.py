"""Give the user a choice instead of a single decree.

Until now the product produced exactly one plan. Even a good plan feels dumb
when there is nothing to compare it against: the user cannot tell whether the
system made a trade-off or simply never considered one.

This module runs the SAME brief through three explicit strategies, measures
each result with the real validator, and states what each option gives up.
It does not invent a fourth "best" answer -- the recommendation is derived
from the user's own preference words, with the evidence shown.

All three runs go through the real `run_plan`, so every number here is
traceable and every plan has passed the same checks.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .agents import build_provider
from .config import Settings, get_settings
from .domain.errors import ShotplanError
from .domain.models import PlanRequest
from .knowledge.tools import KnowledgeTools, get_tools

STRATEGIES: tuple[dict[str, str], ...] = (
    {
        "id": "showcase",
        "label": "出片优先",
        "intent": "把可用时间与预算用满，多走几个外景，优先黄金光线",
    },
    {
        "id": "budget",
        "label": "省钱优先",
        "intent": "压缩预算与外景数量，保留一套完整妆造和最出片的场地",
    },
    {
        "id": "easy",
        "label": "轻松优先",
        "intent": "减少转场与赶场，把时间留给妆造和从容拍摄",
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


def _measure(result: Any, tools: KnowledgeTools) -> dict[str, Any]:
    plan, report = result.plan, result.validation
    shoot = [s for s in plan.slots if s.activity == "shoot"]
    travel = [s for s in plan.slots if s.activity == "travel"]
    scene_travel = 0
    for slot in shoot:
        location = tools.location(plan.city, slot.location_id)
        if location:
            scene_travel += int(location.get("travel_min_from_center", 0))
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
        # Sum of the scenes' own distance from the city centre: this is what
        # "less running around" actually means, not the fixed transfer block.
        "scene_travel_minutes": scene_travel,
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
    delta_score = round(metrics["score"] - baseline["score"], 4)
    if delta_cost:
        out.append(f"{'多花' if delta_cost > 0 else '少花'} {abs(delta_cost)} 元")
    if delta_shoot:
        out.append(f"{'多' if delta_shoot > 0 else '少'} {abs(delta_shoot)} 个拍摄点")
    if delta_minutes:
        out.append(f"拍摄时长{'多' if delta_minutes > 0 else '少'} {abs(delta_minutes)} 分钟")
    if delta_score:
        out.append(f"综合得分{'高' if delta_score > 0 else '低'} {abs(delta_score)}")
    if not out:
        out.append("与出片优先版在可量化指标上持平")
    return out


def _recommend(strategies: list[dict[str, Any]], request: PlanRequest) -> dict[str, Any]:
    """Recommend only among options that actually honour the user's brief."""
    text = " ".join(request.preferences)
    evidence: list[str] = []
    eligible = [s for s in strategies if s.get("within_original_constraints", True)]
    pool = eligible or strategies
    chosen = None
    for strategy_id, words in PREFERENCE_HINTS.items():
        hit = next((w for w in words if w in text), None)
        if hit and any(s["id"] == strategy_id for s in pool):
            evidence.append(hit)
            chosen = strategy_id
            break
    if chosen:
        reason = f"你在偏好里提到「{'、'.join(evidence)}」，按这个方向推荐"
        basis = "preference"
    else:
        viable = [s for s in pool if s["metrics"]["hard_pass"]]
        ranked = viable or pool
        best = max(ranked, key=lambda s: s["metrics"]["score"])
        chosen = best["id"]
        reason = "你没有表达明确倾向，按「先过硬约束、再比综合得分」推荐"
        basis = "score"
        evidence.append(f"最高得分 {best['metrics']['score']}")
    return {"id": chosen, "reason": reason, "basis": basis, "evidence": evidence}


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
        relaxations: list[str] = []
        if variant.hours > base.hours:
            relaxations.append(f"多留 {variant.hours - base.hours:g} 小时")
        if variant.hours < base.hours:
            relaxations.append(f"少留 {base.hours - variant.hours:g} 小时")
        if variant.budget_cny > base.budget_cny:
            relaxations.append(f"多预算 {variant.budget_cny - base.budget_cny} 元")
        if variant.budget_cny < base.budget_cny:
            relaxations.append(f"少预算 {base.budget_cny - variant.budget_cny} 元")
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

            result = run_plan(variant, provider=agent, settings=cfg, max_repairs=0)
            entry["metrics"] = _measure(result, tools)
        except ShotplanError as exc:
            entry["metrics"] = {
                "status": "error", "hard_pass": False, "score": 0.0,
                "scores": {}, "total_cost_cny": 0, "slot_count": 0,
                "shoot_count": 0, "travel_count": 0, "shoot_minutes": 0,
                "issue_codes": [type(exc).__name__], "plan_id": "", "slots": [],
            }
            entry["error"] = str(exc)[:200]
        strategies.append(entry)

    baseline = strategies[0]["metrics"]
    for entry in strategies[1:]:
        entry["tradeoffs"] = _tradeoffs(entry["metrics"], baseline)
    strategies[0]["tradeoffs"] = ["作为对照基准：时间与预算都用满"]

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
