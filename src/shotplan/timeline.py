"""Timeline repairs that belong to code, not to the prompt.

The real-model run left `SHOOT_TOO_SHORT` as the only remaining issue type: the
model packs a shoot into 40 minutes while the booked session still has an hour
of slack after it. Asking the prompt to "watch the block length" is the same
mistake as asking it to watch the sun -- so the block is extended here, and the
result is kept only when validation does not get worse.

Deliberately conservative: only the *trailing* block is extended. Stretching a
mid-timeline block would push everything after it and is where overlaps come
from; that case is left for the repair loop to negotiate.
"""

from __future__ import annotations

from dataclasses import replace

from .domain.constraints import MIN_SHOOT_BLOCK_MINUTES
from .domain.models import Plan, PlanRequest, fmt_minutes, to_minutes


def repair_short_shoots(plan: Plan, req: PlanRequest) -> Plan | None:
    """Extend a too-short trailing shoot into the unused tail of the session.

    Returns None when there is nothing to do or no room to do it. Never mutates
    `plan`.
    """
    if not plan.slots:
        return None
    shoots = [slot for slot in plan.slots if slot.activity == "shoot"]
    if not shoots:
        return None

    last = max(shoots, key=lambda slot: to_minutes(slot.start))
    if last.minutes() >= MIN_SHOOT_BLOCK_MINUTES:
        return None
    if plan.slots[-1] is not last:
        return None

    session_start = min(to_minutes(slot.start) for slot in plan.slots)
    session_end = session_start + int(round(req.hours * 60))
    if to_minutes(last.end) >= session_end:
        return None
    if session_end - to_minutes(last.start) < MIN_SHOOT_BLOCK_MINUTES:
        return None

    slots = list(plan.slots)
    slots[-1] = replace(last, end=fmt_minutes(session_end))
    return replace(plan, slots=slots)
