"""Deterministic follow-ups on the previous brief.

"再松一点" has to mean something specific. Asking a model to interpret it would
add cost, latency and a new way to be wrong; instead this module recognises a
deliberately small vocabulary of adjustments and reports exactly which fields
changed. Anything outside that vocabulary is returned as unrecognised -- asking
the user to edit the form is better than silently guessing.

Two rules keep the arithmetic honest:

* an absolute quantity wins over a vague one. "时间砍一点吧，4 个小时够了" sets
  four hours; the "砍一点" is tone, not a second adjustment to apply on top.
* a number the module cannot place is left unread. It never falls back to a
  default step, because "便宜 300" turning into "cheaper by 300 *or* by one
  step" would be a silent lie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from .domain.models import PlanRequest
from .knowledge.tools import KnowledgeTools, get_tools
from .nlq import clock_times, fmt_clock, numbers_in

BUDGET_STEP_CNY = 300
HOURS_STEP = 1.0
HOURS_CEILING = 12.0
HOURS_FLOOR = 1.0

BUDGET_WORDS = ("预算", "价格", "花费", "钱")
# "便宜一点" is a budget instruction even when the client never says 预算.
PRICE_CUES = ("便宜", "贵", "花钱")
TIME_WORDS = ("时间", "时长")
RAISE_WORDS = ("多", "加", "提高", "宽裕", "涨")
# "长" only means "longer" for time; on a budget it means nothing.
TIME_RAISE_WORDS = (*RAISE_WORDS, "长", "延")
LOWER_WORDS = ("砍", "降", "减", "便宜", "省", "少", "压", "缩")
HEDGE_WORDS = ("一点", "一些", "点", "一下", "些", "点儿")
TO_MARKERS = ("到", "至")
# "4个小时" and "4 个小时" are the same unit; the classifier must not depend on
# whether the client typed the optional 个 or a space.
MONEY_UNIT = re.compile(r"^\s*(?:元|块|人民币)")
TIME_UNIT = re.compile(r"^\s*个?\s*(?:小时|钟头|h)")

RELAX_WORDS = ("松一点", "再松", "轻松", "别太累", "不要太累", "少走", "少转场")
QUIET_WORDS = ("人少", "安静", "清净")

NEGATIONS = ("不要", "不想要", "不喜欢", "别要", "去掉", "换掉", "不穿", "不拍", "不想")
DEADLINE_CUES = (
    "赶",
    "高铁",
    "火车",
    "飞机",
    "航班",
    "班车",
    "返程",
    "回去",
    "得走",
    "要走",
    "得回",
    "要回",
)
DEADLINE_PREFERENCE = re.compile(r"^\d{2}:\d{2}前结束$")


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


@dataclass(frozen=True)
class Amounts:
    """What the numbers in one sentence say, already placed on a field."""

    budget_absolute: int | None = None
    budget_delta: int | None = None
    hours_absolute: float | None = None
    hours_delta: float | None = None

    @property
    def speaks_about_budget(self) -> bool:
        return self.budget_absolute is not None or self.budget_delta is not None

    @property
    def speaks_about_hours(self) -> bool:
        return self.hours_absolute is not None or self.hours_delta is not None


def _unit_of(after: str) -> str | None:
    """Which unit, if any, starts right after the number."""
    if TIME_UNIT.match(after):
        return "hours"
    if MONEY_UNIT.match(after):
        return "budget"
    return None


def read_amounts(text: str) -> Amounts:
    """Place every number in the sentence on a field, or leave it unread.

    The unit decides first, because it is what the client actually typed:
    "4 个小时" is a length whatever verb precedes it, "300 元" is money. Only a
    number with no unit falls back to the words around it.
    """
    budget_absolute: int | None = None
    budget_delta: int | None = None
    hours_absolute: float | None = None
    hours_delta: float | None = None

    for quantity in numbers_in(text):
        before = text[max(0, quantity.start - 12) : quantity.start]
        after = text[quantity.end : quantity.end + 6]

        unit = _unit_of(after)
        if unit == "hours":
            field, explicit_unit = "hours", True
        elif unit == "budget":
            field, explicit_unit = "budget", True
        elif any(word in before for word in TIME_WORDS):
            field, explicit_unit = "hours", False
        elif any(word in before for word in BUDGET_WORDS):
            field, explicit_unit = "budget", False
        elif any(word in before for word in LOWER_WORDS):
            # "便宜 300" -- a bare number after a lowering word is money off.
            field, explicit_unit = "budget", False
        else:
            continue

        goes_to_target = any(marker in before for marker in TO_MARKERS)
        absolute = explicit_unit or goes_to_target
        lowering = any(word in before for word in LOWER_WORDS)
        raising = any(word in before for word in RAISE_WORDS)

        if field == "budget":
            money = int(round(quantity.value))
            if absolute or not (lowering or raising):
                budget_absolute = money
            elif lowering:
                budget_delta = -money
            else:
                budget_delta = money
        else:
            length = float(quantity.value)
            if length <= 0:
                continue
            if absolute or not (lowering or raising):
                hours_absolute = length
            elif lowering:
                hours_delta = -length
            else:
                hours_delta = length

    return Amounts(budget_absolute, budget_delta, hours_absolute, hours_delta)


def _clamp_hours(value: float) -> float:
    return max(HOURS_FLOOR, min(HOURS_CEILING, round(value, 2)))


def _apply_amounts(
    request: PlanRequest, text: str, changes: list[str]
) -> tuple[PlanRequest, Amounts]:
    amounts = read_amounts(text)
    updated = request

    if amounts.budget_absolute is not None:
        target = max(0, amounts.budget_absolute)
        if target != request.budget_cny:
            changes.append(f"预算 {request.budget_cny} → {target} 元")
            updated = replace(updated, budget_cny=target)
    elif amounts.budget_delta is not None:
        target = max(0, request.budget_cny + amounts.budget_delta)
        if target != request.budget_cny:
            sign = "增" if amounts.budget_delta > 0 else "减"
            changes.append(
                f"预算 {request.budget_cny} → {target} 元（{sign} {abs(amounts.budget_delta)}）"
            )
            updated = replace(updated, budget_cny=target)

    if amounts.hours_absolute is not None:
        target_hours = _clamp_hours(amounts.hours_absolute)
        if target_hours != updated.hours:
            changes.append(f"时长 {updated.hours:g} → {target_hours:g} 小时")
            updated = replace(updated, hours=target_hours)
    elif amounts.hours_delta is not None:
        target_hours = _clamp_hours(updated.hours + amounts.hours_delta)
        if target_hours != updated.hours:
            changes.append(f"时长 {updated.hours:g} → {target_hours:g} 小时")
            updated = replace(updated, hours=target_hours)

    return updated, amounts


def _apply_verbal(
    request: PlanRequest, text: str, amounts: Amounts, changes: list[str]
) -> PlanRequest:
    """Vague adjustments, skipped for any field a number already settled."""
    updated = request

    if not amounts.speaks_about_budget and (
        any(word in text for word in BUDGET_WORDS)
        or any(word in text for word in PRICE_CUES)
    ):
        if any(word in text for word in LOWER_WORDS):
            target = max(0, updated.budget_cny - BUDGET_STEP_CNY)
            if target != updated.budget_cny:
                changes.append(
                    f"预算 {updated.budget_cny} → {target} 元（按一步 {BUDGET_STEP_CNY} 元收）"
                )
                updated = replace(updated, budget_cny=target)
        elif any(word in text for word in RAISE_WORDS):
            target = updated.budget_cny + BUDGET_STEP_CNY
            changes.append(f"预算 {updated.budget_cny} → {target} 元")
            updated = replace(updated, budget_cny=target)

    if not amounts.speaks_about_hours and any(word in text for word in TIME_WORDS):
        if any(word in text for word in LOWER_WORDS):
            target_hours = _clamp_hours(updated.hours - HOURS_STEP)
            if target_hours != updated.hours:
                changes.append(f"时长 {updated.hours:g} → {target_hours:g} 小时")
                updated = replace(updated, hours=target_hours)
        elif any(word in text for word in TIME_RAISE_WORDS):
            target_hours = _clamp_hours(updated.hours + HOURS_STEP)
            if target_hours != updated.hours:
                changes.append(f"时长 {updated.hours:g} → {target_hours:g} 小时")
                updated = replace(updated, hours=target_hours)

    if any(word in text for word in RELAX_WORDS):
        updated = _add_preferences(updated, ["轻松", "少转场"], changes)

    if any(word in text for word in QUIET_WORDS):
        updated = _add_preferences(updated, ["人少", "安静"], changes)

    return updated


def _style_mentions(
    text: str, tools: KnowledgeTools
) -> list[tuple[int, str, bool]]:
    """Every style name in the sentence, with whether it was negated.

    Position decides. "不要唐制了，换宋制" mentions 唐制 after a negation and 宋制
    after 换; reading the whole sentence for "不要" would reject both.
    """
    mentions: list[tuple[int, str, bool]] = []
    for style in tools.styles():
        style_id = str(style.get("id") or "")
        names = [str(style.get("name") or "")] + [
            str(alias) for alias in (style.get("aliases") or [])
        ]
        for name in names:
            if not name:
                continue
            for match in re.finditer(re.escape(name), text):
                prefix = text[max(0, match.start() - 4) : match.start()]
                negated = any(word in prefix for word in NEGATIONS)
                mentions.append((match.start(), style_id, negated))
    mentions.sort(key=lambda item: item[0])
    return mentions


def _apply_styles(
    request: PlanRequest,
    text: str,
    tools: KnowledgeTools,
    changes: list[str],
    refusals: list[str],
) -> PlanRequest:
    mentions = _style_mentions(text, tools)
    if not mentions:
        return request

    final: dict[str, bool] = {}
    for _, style_id, negated in mentions:
        if style_id:
            final[style_id] = not negated

    wanted = [style_id for style_id in request.styles if final.get(style_id, True)]
    for style_id, keep in final.items():
        if keep and style_id not in wanted:
            wanted.append(style_id)
    if wanted == request.styles:
        return request
    if not wanted:
        # An empty style list is not a plan. Refuse in words, and above all do
        # not report this as a recognised adjustment -- nothing was changed.
        refusals.append("这条会去掉全部风格，至少保留一个；本次没有改动。")
        return request

    names = {}
    for style_id in {*request.styles, *wanted}:
        style = tools.style(style_id) or {}
        names[style_id] = str(style.get("name") or style_id)
    dropped = [names[s] for s in request.styles if s not in wanted]
    added = [names[s] for s in wanted if s not in request.styles]
    parts = []
    if dropped:
        parts.append("去掉 " + "、".join(dropped))
    if added:
        parts.append("换成 " + "、".join(added))
    changes.append("风格：" + "，".join(parts))
    return replace(request, styles=wanted)


def _apply_deadline(
    request: PlanRequest, text: str, changes: list[str]
) -> PlanRequest:
    """A train to catch is a constraint, not small talk.

    Only sentences that mention the journey itself ("赶…高铁") are read. "下午
    五点光线好" has the same clock time and no deadline in it.
    """
    if not any(cue in text for cue in DEADLINE_CUES):
        return request
    times = clock_times(text)
    if not times:
        return request

    preference = f"{fmt_clock(min(times))}前结束"
    kept = [p for p in request.preferences if not DEADLINE_PREFERENCE.match(p)]
    if preference in kept:
        return request
    changes.append(f"结束时间：{preference}")
    return replace(request, preferences=[*kept, preference])


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
    refusals: list[str] = []
    updated, amounts = _apply_amounts(request, text, changes)
    updated = _apply_verbal(updated, text, amounts, changes)
    updated = _apply_styles(updated, text, tools, changes, refusals)
    updated = _apply_deadline(updated, text, changes)

    city = _match_city(text, tools)
    if city and city != updated.city:
        changes.append(f"城市 {updated.city} → {city}")
        updated = replace(updated, city=city)

    if not changes:
        return Followup(
            updated.normalized(),
            False,
            [],
            refusals[0]
            if refusals
            else "没识别出可执行的调整。可以直接改表单，或说「预算多一点」「再松一点」「换到杭州」。",
        )
    return Followup(
        updated.normalized(),
        True,
        changes,
        "已按上一轮条件调整：" + "；".join(changes),
    )
