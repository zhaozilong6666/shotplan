"""Turn a refusal into a choice.

`check_feasibility` is deliberately strict, but a bare "impossible" is a dead
end: the user has to guess which knob to turn. This module proposes the
smallest set of changes that makes the brief executable -- and *verifies every
proposal by re-running the same preflight* instead of asserting it works.

No LLM, no guessing about what "probably" helps: an option is only returned
when `check_feasibility` says the modified request is feasible.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .domain.feasibility import check_feasibility
from .domain.models import PlanRequest
from .knowledge.tools import KnowledgeTools, get_tools


@dataclass
class Negotiation:
    label: str
    changes: dict[str, Any]
    request: PlanRequest
    tradeoff: str
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "changes": self.changes,
            "request": self.request.to_dict(),
            "tradeoff": self.tradeoff,
            "verified": self.verified,
        }


def _variants(req: PlanRequest, minimums: dict[str, Any], city_ids: list[str]) -> list[Negotiation]:
    """Candidate single-axis repairs, plus one combined repair."""
    out: list[Negotiation] = []
    min_budget = int(minimums.get("min_budget_cny", 0))
    min_hours = float(minimums.get("min_hours", 0))

    if req.budget_cny < min_budget:
        candidate = replace(req, budget_cny=min_budget)
        out.append(Negotiation(
            label=f"把预算提到 {min_budget} 元",
            changes={"budget_cny": min_budget},
            request=candidate,
            tradeoff=f"多花 {min_budget - req.budget_cny} 元，换来一套可执行方案",
        ))
    if req.hours < min_hours:
        candidate = replace(req, hours=min_hours)
        out.append(Negotiation(
            label=f"把拍摄时长延长到 {min_hours:g} 小时",
            changes={"hours": min_hours},
            request=candidate,
            tradeoff=f"多留 {min_hours - req.hours:g} 小时给妆造与转场",
        ))
    if req.city not in city_ids and city_ids:
        fallback = "xian" if "xian" in city_ids else sorted(city_ids)[0]
        candidate = replace(req, city=fallback)
        out.append(Negotiation(
            label=f"改用已收录的城市：{fallback}",
            changes={"city": fallback},
            request=candidate,
            tradeoff="换城市，其余条件不变",
        ))
    conflicting = sorted(set(req.must_include) & set(req.avoid))
    if conflicting:
        candidate = replace(req, avoid=[a for a in req.avoid if a not in conflicting])
        out.append(Negotiation(
            label="解除自相矛盾的地点约束",
            changes={"avoid": candidate.avoid},
            request=candidate,
            tradeoff=f"不再排除：{', '.join(conflicting)}",
        ))
    if req.must_include:
        # Let go of a required stop: a plan with fewer stops beats no plan.
        candidate = replace(req, must_include=[])
        out.append(Negotiation(
            label="取消必去地点，让系统自由选点",
            changes={"must_include": []},
            request=candidate,
            tradeoff=f"放开 {len(req.must_include)} 个指定地点",
        ))

    combined_changes: dict[str, Any] = {}
    if req.budget_cny < min_budget:
        combined_changes["budget_cny"] = min_budget
    if req.hours < min_hours:
        combined_changes["hours"] = min_hours
    if req.city not in city_ids and city_ids:
        combined_changes["city"] = "xian" if "xian" in city_ids else sorted(city_ids)[0]
    if len(combined_changes) > 1:
        out.append(Negotiation(
            label="一次性按最低条件调整",
            changes=combined_changes,
            request=replace(req, **combined_changes),
            tradeoff="同时调整多项，改动最大但一次到位",
        ))
        # A roomier alternative: the minimum makes it *possible*, not pleasant.
        roomy = dict(combined_changes)
        if req.budget_cny < min_budget:
            roomy["budget_cny"] = min_budget + 300
        if req.hours < min_hours:
            roomy["hours"] = min_hours + 1
        if roomy != combined_changes:
            out.append(Negotiation(
                label="留点余量（预算 +300 元、时长 +1 小时）",
                changes=roomy,
                request=replace(req, **roomy),
                tradeoff="更从容的妆造与转场时间，代价是多花钱与时间",
            ))
    return out


def negotiate(
    request: PlanRequest, *, tools: KnowledgeTools | None = None, limit: int = 3
) -> list[Negotiation]:
    """Only return repairs that actually pass the preflight."""
    tools = tools or get_tools()
    report = check_feasibility(request, tools)
    if report.feasible:
        return []
    proposals = _variants(request.normalized(), report.minimums, tools.city_ids())
    verified: list[Negotiation] = []
    seen: set[str] = set()
    for option in proposals:
        key = repr(sorted(option.changes.items()))
        if key in seen:
            continue
        seen.add(key)
        verdict = check_feasibility(option.request, tools)
        if not verdict.feasible:
            continue
        option.verified = True
        verified.append(option)
    # Fewer changed fields first: the smallest repair should be the default.
    verified.sort(key=lambda o: (len(o.changes), sum(len(str(v)) for v in o.changes.values())))
    return verified[:limit]
