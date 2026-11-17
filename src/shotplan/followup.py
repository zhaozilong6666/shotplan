"""Deterministic follow-ups on the previous brief.

"再松一点" has to mean something specific. Asking a model to interpret it would
add cost, latency and a new way to be wrong; instead this module recognises a
deliberately small vocabulary of adjustments and reports exactly which fields
changed. Anything outside that vocabulary is returned as unrecognised -- asking
the user to edit the form is better than silently guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .domain.models import PlanRequest
from .knowledge.tools import KnowledgeTools, get_tools

BUDGET_STEP_CNY = 300
HOURS_STEP = 1.0

MORE_BUDGET_WORDS = ("预算", "价格", "花费")
MORE_BUDGET_VERBS = ("多", "加", "提高", "宽裕", "高")
CHEAPER_WORDS = ("紧", "省", "便宜", "少")
MORE_TIME_WORDS = ("时间", "时长")
MORE_TIME_VERBS = ("多", "长", "宽裕", "松")
RELAX_WORDS = ("松一点", "再松", "轻松", "别太累", "不要太累", "少走", "少转场")
QUIET_WORDS = ("人少", "安静", "清净")


@dataclass
class Followup:
    request: PlanRequest
    recognized: bool
    changes: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "recognized": self.recognized,
            "changes": self.changes,
            "message": self.message,
        }


def _add_preferences(
    request: PlanRequest, words: list[str], changes: list[str]
) -> PlanRequest:
    added = [word for word in words if word not in request.preferences]
    if not added:
        return request
    changes.append("增加偏好：" + "、".join(added))
    return replace(request, preferences=[*request.preferences, *added])


def _match_city(text: str, tools: KnowledgeTools) -> str | None:
    for city_id in tools.city_ids():
        name = str(tools.city(city_id).get("name") or "")
        if name and name in text:
            return city_id
    return None


def apply_followup(
    previous: PlanRequest, text: str, *, tools: KnowledgeTools | None = None
) -> Followup:
    """Apply one recognised adjustment to the previous brief. Never guesses."""
    tools = tools or get_tools()
    request = previous.normalized()
    text = (text or "").strip()
    if not text:
        return Followup(request, False, [], "没有收到调整内容。")

    changes: list[str] = []
    updated = request

    raises_budget = any(word in text for word in MORE_BUDGET_WORDS) and any(
        verb in text for verb in MORE_BUDGET_VERBS
    )
    if raises_budget:
        target = request.budget_cny + BUDGET_STEP_CNY
        changes.append(f"预算 {request.budget_cny} → {target} 元")
        updated = replace(updated, budget_cny=target)
    elif any(word in text for word in MORE_BUDGET_WORDS) and any(
        word in text for word in CHEAPER_WORDS
    ):
        updated = _add_preferences(updated, ["控制预算", "省钱"], changes)

    if any(word in text for word in MORE_TIME_WORDS) and any(
        verb in text for verb in MORE_TIME_VERBS
    ):
        target_hours = min(12.0, request.hours + HOURS_STEP)
        if target_hours > request.hours:
            changes.append(f"时长 {request.hours:g} → {target_hours:g} 小时")
            updated = replace(updated, hours=target_hours)

    if any(word in text for word in RELAX_WORDS):
        updated = _add_preferences(updated, ["轻松", "少转场"], changes)

    if any(word in text for word in QUIET_WORDS):
        updated = _add_preferences(updated, ["人少", "安静"], changes)

    city = _match_city(text, tools)
    if city and city != updated.city:
        changes.append(f"城市 {updated.city} → {city}")
        updated = replace(updated, city=city)

    if not changes:
        return Followup(
            updated.normalized(),
            False,
            [],
            "没识别出可执行的调整。可以直接改表单，或说「预算多一点」「再松一点」「换到杭州」。",
        )
    return Followup(
        updated.normalized(),
        True,
        changes,
        "已按上一轮条件调整：" + "；".join(changes),
    )
