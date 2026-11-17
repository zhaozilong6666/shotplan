"""Pre-generation feasibility checks: a second decision we refuse to delegate to an LLM.

Some briefs cannot be satisfied at all: the budget is below the cheapest
styling, the booking is too short for makeup + transfer + one shoot, the city
is not in the knowledge base, or must_include contradicts avoid. Asking a model
to "try anyway" guarantees a plausible-looking plan that fails validation. So
these are detected deterministically BEFORE generation. See ADR 0007.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .constraints import (
    MIN_FEASIBLE_HOURS,
    MIN_SHOOT_BLOCK_MINUTES,
    MIN_TRANSFER_COST_CNY,
    MIN_TRANSFER_MINUTES,
    STYLING_FLOOR_MINUTES,
)
from .models import PlanRequest


@dataclass
class FeasibilityIssue:
    code: str
    message: str
    shortfall: str
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeasibilityReport:
    feasible: bool
    reasons: list[FeasibilityIssue] = field(default_factory=list)
    minimums: dict[str, Any] = field(default_factory=dict)

    @property
    def headline(self) -> str:
        return "；".join(reason.message for reason in self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "headline": self.headline,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "minimums": dict(self.minimums),
        }


def _is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return True


def check_feasibility(request: PlanRequest, tools: Any) -> FeasibilityReport:
    """Return a structured verdict. Pure function of the brief + knowledge base."""
    req = request.normalized()
    reasons: list[FeasibilityIssue] = []
    city_ids = tools.city_ids()

    min_styling = min(int(style["price_cny"][0]) for style in tools.styles())
    locations = tools.locations(req.city) if req.city in city_ids else []
    min_location = min((int(item["cost_cny"]) for item in locations), default=0)
    min_budget = min_styling + min_location + MIN_TRANSFER_COST_CNY

    if req.city not in city_ids:
        reasons.append(
            FeasibilityIssue(
                code="UNSUPPORTED_CITY",
                message=f"知识库中没有城市 {req.city!r}",
                shortfall=f"可用城市：{', '.join(city_ids)}",
                suggestion="改用知识库内的城市，或先在 locations.json 里补充该城市",
            )
        )

    if req.budget_cny < min_budget:
        reasons.append(
            FeasibilityIssue(
                code="BUDGET_BELOW_MINIMUM",
                message=(f"预算 {req.budget_cny} 元低于最低可执行方案成本 "
                         f"{min_budget} 元"),
                shortfall=f"还差 {min_budget - req.budget_cny} 元",
                suggestion=(f"把预算提高到至少 {min_budget} 元，或改用最简妆造套餐"
                            "与免费场景"),
            )
        )

    if req.hours < MIN_FEASIBLE_HOURS:
        reasons.append(
            FeasibilityIssue(
                code="TIME_BELOW_MINIMUM",
                message=(f"预约时长 {req.hours} 小时不足以完成"
                         f"「妆造 → 转场 → 拍摄」最短闭环"),
                shortfall=f"至少需要 {MIN_FEASIBLE_HOURS} 小时",
                suggestion=(f"把预约延长到 {MIN_FEASIBLE_HOURS} 小时以上，"
                            "或改为纯室内单场景拍摄"),
            )
        )

    if not _is_iso_date(req.date):
        reasons.append(
            FeasibilityIssue(
                code="DATE_INVALID",
                message=f"日期 {req.date!r} 不是合法的 YYYY-MM-DD",
                shortfall="无法确定季节、日照与黄金光线窗口",
                suggestion="按 2026-10-04 这样的格式填写日期（含真实存在的日）",
            )
        )

    conflicting = sorted(set(req.must_include) & set(req.avoid))
    if conflicting:
        reasons.append(
            FeasibilityIssue(
                code="CONFLICTING_CONSTRAINTS",
                message=f"同一地点既被要求包含又被排除：{', '.join(conflicting)}",
                shortfall="约束互相矛盾，无法同时满足",
                suggestion="从 must_include 或 avoid 里移除冲突的地点 id",
            )
        )

    known = {item["id"] for item in locations}
    unknown_required = sorted(set(req.must_include) - known) if req.city in city_ids else []
    if unknown_required:
        reasons.append(
            FeasibilityIssue(
                code="UNKNOWN_REQUIRED_LOCATION",
                message=f"必去地点不在知识库中：{', '.join(unknown_required)}",
                shortfall="无法为不存在的地点安排行程",
                suggestion="改用知识库内的地点 id，或先在 locations.json 里补充",
            )
        )

    if known and known <= set(req.avoid):
        reasons.append(
            FeasibilityIssue(
                code="ALL_LOCATIONS_EXCLUDED",
                message="该城市的全部地点都被 avoid 排除，没有任何可用场地",
                shortfall=f"可用地点数 {len(known - set(req.avoid))}",
                suggestion="从 avoid 里移除至少一个地点，或换一座城市",
            )
        )

    # Arithmetic the other checks do not cover: the required places each carry
    # their own dwell time, and two of them can add up to more than the booking
    # once styling and transfers are counted. The planner used to discover this
    # only after generation, by dropping one required place -- producing a
    # plausible plan that then failed MISSING_REQUIRED_LOCATION. A brief no plan
    # can satisfy should be refused, not half-executed.
    #
    # The estimate is deliberately optimistic, because the question is "does any
    # plan exist", not "does the ideal plan fit". Two details decide whether this
    # refuses impossible briefs or merely annoying ones:
    #
    # - dwell time uses the minimum shoot block, not the location's suggested
    #   duration. `min_minutes` is how long a place deserves, not the least that
    #   can be shot there; treating it as a floor rejected a two-hour booking of
    #   a ninety-minute wall that the planner can cover in sixty.
    # - styling uses the floor rather than the style's standard duration, and one
    #   transfer sits between places rather than one per place.
    required_places = [item for item in locations if item["id"] in set(req.must_include)]
    if required_places:
        shoot_minutes = MIN_SHOOT_BLOCK_MINUTES * len(required_places)
        styling = STYLING_FLOOR_MINUTES
        transfer = MIN_TRANSFER_MINUTES * max(0, len(required_places) - 1)
        needed = shoot_minutes + styling + transfer
        available = int(req.hours * 60)
        if available < needed:
            reasons.append(
                FeasibilityIssue(
                    code="TIME_BELOW_REQUIRED_LOCATIONS",
                    message=(
                        f"{len(required_places)} 个必去地点至少需 {shoot_minutes} 分钟拍摄，"
                        f"加最少妆造 {styling} 分钟、转场 {transfer} 分钟，共 {needed} 分钟，"
                        f"超过预约的 {available} 分钟"
                    ),
                    shortfall=f"还差 {needed - available} 分钟",
                    suggestion="延长预约，或从 must_include 里去掉一个地点",
                )
            )

    minimums = {
        "min_budget_cny": min_budget,
        "min_hours": MIN_FEASIBLE_HOURS,
        "min_styling_cny": min_styling,
        "min_location_cny": min_location,
        "transfer_cost_cny": MIN_TRANSFER_COST_CNY,
        "supported_cities": city_ids,
    }
    return FeasibilityReport(feasible=not reasons, reasons=reasons, minimums=minimums)
