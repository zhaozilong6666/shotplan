"""Explain a validated plan from evidence the pipeline already has.

The planner is deterministic but opaque: a slot says *what* happens, not *why*
this location at this time. The cheapest honest fix is not another model call —
it is to read back the same facts the validator used (style tags, the solar
window, crowd level, indoor flag) and name them. Every reason here is
checkable against ``locations.json`` / ``styles.json``, and the function is
pure, so the explanation cannot drift from the plan it describes.

Only ``shoot`` blocks get an entry. Makeup/travel/rest are consequences of the
shoot schedule, and pretending they carry independent rationale would be noise.
"""

from __future__ import annotations

from typing import Any

from .domain.models import Plan, PlanRequest, fmt_minutes, to_minutes
from .knowledge.tools import KnowledgeTools

CROWD_REASONS = {
    "low": "知识库标记为低客流",
    "medium": "知识库标记为中等客流",
    "high": "知识库标记为高客流，建议避开节假日正午",
}
WEATHER_REASONS = {
    "low": "天气风险低",
    "medium": "天气风险中等",
    "high": "天气风险高，出发前需要复核",
}


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _style_match(
    location_tags: set[str], requested: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(style labels, matching tags) shared by the request and this location."""
    styles: list[str] = []
    tags: list[str] = []
    for style in requested:
        hit = location_tags & set(style.get("suitable_tags") or [])
        if hit:
            styles.append(f"{style.get('name') or style['id']}（{style['id']}）")
            tags.extend(sorted(hit))
    return list(dict.fromkeys(styles)), list(dict.fromkeys(tags))


def explain_plan(
    plan: Plan, request: PlanRequest, kb: KnowledgeTools
) -> list[dict[str, Any]]:
    """One evidence-backed entry per shoot block. Never invents a fact."""
    if not plan.slots:
        return []

    requested_styles = [s for sid in request.styles if (s := kb.style(sid))]
    light_start, light_end = kb.light_window(plan.date or request.date, plan.city)
    window_start, window_end = to_minutes(light_start), to_minutes(light_end)

    entries: list[dict[str, Any]] = []
    for index, slot in enumerate(plan.slots):
        if slot.activity != "shoot" or not slot.location_id or slot.location_id == "-":
            continue
        location = kb.location(plan.city, slot.location_id)
        if location is None:
            # A validated plan should never reach this branch; keeping it makes
            # the explanation harmless when called on a draft (e.g. a reviewer
            # inspecting a failed repair) instead of raising on the same defect
            # the validator is about to report.
            entries.append(
                {
                    "slot_index": index,
                    "start": slot.start,
                    "end": slot.end,
                    "label": slot.label,
                    "location_id": slot.location_id,
                    "reasons": ["地点不在当前知识库中，无法给出可核对的选择理由"],
                }
            )
            continue

        reasons: list[str] = []
        if slot.location_id in request.must_include:
            reasons.append("你点名要求包含这个地点")

        styles, tags = _style_match(set(location.get("tags") or []), requested_styles)
        if styles:
            reasons.append(
                f"风格匹配：{'、'.join(styles)} 适合 {'、'.join(tags)} 标签"
            )

        start, end = to_minutes(slot.start), to_minutes(slot.end)
        overlap = _overlap(start, end, window_start, window_end)
        if end > start and overlap == end - start:
            reasons.append(
                f"光线：{fmt_minutes(window_start)}–{fmt_minutes(window_end)} 是当地黄金窗口"
                "（NOAA 日落前 120 分钟），本段全程落在窗口内"
            )
        elif overlap > 0:
            reasons.append(
                f"光线：本段有 {overlap} 分钟落在黄金窗口 "
                f"{fmt_minutes(window_start)}–{fmt_minutes(window_end)} 内"
            )

        if location.get("indoor"):
            reasons.append("室内场景：不受日落时间限制，可作为天气备选")

        if crowd := CROWD_REASONS.get(str(location.get("crowd", ""))):
            reasons.append(f"人流：{crowd}")
        if risk := WEATHER_REASONS.get(str(location.get("weather_risk", ""))):
            reasons.append(f"天气：{risk}")

        if not reasons:
            reasons.append("由候选搜索按风格、光线、人流、转场与预算综合选出")

        entries.append(
            {
                "slot_index": index,
                "start": slot.start,
                "end": slot.end,
                "label": slot.label,
                "location_id": slot.location_id,
                "reasons": reasons,
            }
        )
    return entries


__all__ = ["explain_plan"]
