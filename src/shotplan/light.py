"""Deterministic light scheduling: the code owns the golden window.

The 80-case real-model run put ``LIGHT_MISUSE`` at 31 of 46 issues while format
and constraint comprehension were fine. The model does not reason about where
the sun will be -- so the golden window stops being a prompt suggestion and
becomes a post-generation alignment step.

The window itself is a month-level seasonal approximation shared by every city:
latitude/longitude are deliberately not used yet. It is precise enough to move
an obviously wrong 11:00 shoot out of the midday sun, but it is not an
astronomical almanac. Beijing and Chengdu currently get the same window, which
is a known limitation rather than a claim about the sun.

Two rules keep this honest:

* A brief that asks to finish by noon is never aligned. An empty golden window
  is then the correct answer, not a defect (see ``wants_morning_finish``).
* An aligned plan is only kept when it validates at least as well as the
  original, so fixing the light can never silently introduce a time or budget
  violation.
"""

from __future__ import annotations

from dataclasses import replace

from .domain.constraints import MIN_SHOOT_BLOCK_MINUTES, wants_morning_finish
from .domain.models import Plan, PlanRequest, TimeSlot, fmt_minutes, to_minutes
from .knowledge.tools import KnowledgeTools


def _overlap_minutes(slot: TimeSlot, start: int, end: int) -> int:
    return max(0, min(to_minutes(slot.end), end) - max(to_minutes(slot.start), start))


def _outdoor_shoots(plan: Plan, kb: KnowledgeTools) -> list[TimeSlot]:
    out: list[TimeSlot] = []
    for slot in plan.slots:
        if slot.activity != "shoot":
            continue
        location = kb.location(plan.city, slot.location_id)
        if location and not location["indoor"]:
            out.append(slot)
    return out


DAY_EARLIEST = 6 * 60      # nobody shoots before 06:00
DAY_LATEST = 22 * 60       # or finishes after 22:00
GRACE_MINUTES = 15         # same grace the validator allows on TIME_OVERRUN


def align_light_window(
    plan: Plan, req: PlanRequest, kb: KnowledgeTools
) -> Plan | None:
    """Shift the whole timeline so the trailing outdoor shoot lands in the window.

    Shifting the *whole* timeline, not just the last block, is the point: a
    six-hour session that starts at 08:00 ends at 14:00 and simply cannot
    contain a 16:00-18:00 window. The same six hours starting at 12:00 can.
    Total duration and the order of activities are preserved, so nothing else
    about the plan changes.

    Returns None when no alignment is warranted or possible -- the caller keeps
    the original plan. Never mutates `plan`.
    """
    if wants_morning_finish(list(req.preferences)):
        return None
    shoots = _outdoor_shoots(plan, kb)
    if not shoots or not plan.slots:
        return None

    light_start, light_end = kb.light_window(plan.date or req.date, plan.city)
    window_start, window_end = to_minutes(light_start), to_minutes(light_end)
    if any(_overlap_minutes(slot, window_start, window_end) > 0 for slot in shoots):
        return None

    first_start = to_minutes(plan.slots[0].start)
    last_end = max(to_minutes(slot.end) for slot in plan.slots)
    span = last_end - first_start
    if span > req.hours * 60 + GRACE_MINUTES:
        return None

    last_shoot = max(shoots, key=lambda slot: to_minutes(slot.start))
    shoot_start = to_minutes(last_shoot.start)
    shoot_end = to_minutes(last_shoot.end)

    # Any shift in this interval puts the whole block inside the window.
    lo = window_start - shoot_start
    hi = window_end - shoot_end
    if lo > hi:
        # The block is longer than the window: keep as much as fits at the start.
        if window_end - window_start < MIN_SHOOT_BLOCK_MINUTES:
            return None
        return _shift(plan, req, window_start - shoot_start, kb, window_start, window_end)

    return _shift(plan, req, lo, kb, window_start, window_end)


def _shift(
    plan: Plan, req: PlanRequest, delta: int, kb: KnowledgeTools,
    window_start: int, window_end: int,
) -> Plan | None:
    """Translate every slot by `delta` minutes, or return None if that is absurd."""
    if delta == 0:
        return None
    shifted: list[TimeSlot] = []
    for slot in plan.slots:
        start = to_minutes(slot.start) + delta
        end = to_minutes(slot.end) + delta
        if start < DAY_EARLIEST or end > DAY_LATEST:
            return None
        shifted.append(replace(slot, start=fmt_minutes(start), end=fmt_minutes(end)))

    span = to_minutes(shifted[-1].end) - to_minutes(shifted[0].start)
    if span > req.hours * 60 + GRACE_MINUTES:
        return None
    # Re-check the light itself: the whole point of the shift.
    if not any(
        _overlap_minutes(slot, window_start, window_end) > 0
        for slot in shifted
        if slot.activity == "shoot"
    ):
        return None
    return replace(plan, slots=shifted)
