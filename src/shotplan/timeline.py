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

import re
from dataclasses import replace

from .domain.constraints import min_shoot_block_minutes, styling_minutes_for
from .domain.models import Plan, PlanRequest, TimeSlot, fmt_minutes, to_minutes

_DURATION_IN_TEXT = re.compile(r"(\d+)\s*分钟")


def _restate_duration(text: str, old_minutes: int, new_minutes: int) -> str:
    """Rewrite "5 分钟" to "30 分钟" when that number was the block's length.

    Only the shoot's own label is passed through here: it is the one place where
    a model reliably restates the block length, and leaving it next to a
    30-minute block would make the plan contradict itself.
    """
    return _DURATION_IN_TEXT.sub(
        lambda match: f"{new_minutes} 分钟"
        if int(match.group(1)) == old_minutes
        else match.group(0),
        text,
    )


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
    if last.minutes() >= min_shoot_block_minutes(req.hours):
        return None
    if plan.slots[-1] is not last:
        return None

    session_start = min(to_minutes(slot.start) for slot in plan.slots)
    session_end = session_start + int(round(req.hours * 60))
    if to_minutes(last.end) >= session_end:
        return None
    if session_end - to_minutes(last.start) < min_shoot_block_minutes(req.hours):
        return None

    slots = list(plan.slots)
    slots[-1] = replace(last, end=fmt_minutes(session_end))
    return replace(plan, slots=slots)


def compress_styling_for_shoots(plan: Plan, req: PlanRequest) -> Plan | None:
    """Take time from an oversized styling block and give it to the shoot after it.

    The real-model run produced "85 minutes of styling, 5 minutes of shooting"
    for a 1.5-hour booking: the model copied the style's standard duration and
    let the shoot absorb the shortfall. The booked hours are the budget, and the
    photoshoot is what the client is paying for -- so styling is the block that
    gives time back, down to ``styling_minutes_for``.

    Deliberately narrow: only a run of styling blocks that ends exactly when the
    short shoot starts, and the shoot's end never moves. Anything else (a
    transfer in between, a shoot in the middle of the timeline) is left to the
    repair loop, because moving later slots is where overlaps come from.

    The run matters more than a single block: the real model split 85 minutes of
    styling into "60 min makeup" + "25 min costume", so looking at only the last
    block concluded there was nothing to compress.
    """
    if not plan.slots:
        return None
    floor = min_shoot_block_minutes(req.hours)
    slots = list(plan.slots)

    for index, slot in enumerate(slots):
        if slot.activity != "shoot" or index == 0:
            continue
        short = floor - slot.minutes()
        if short <= 0:
            continue

        # Walk back over the contiguous styling run touching this shoot.
        run: list[int] = []
        cursor = index - 1
        while (
            cursor >= 0
            and slots[cursor].activity == "makeup"
            and to_minutes(slots[cursor].end) == to_minutes(slots[cursor + 1].start)
        ):
            run.append(cursor)
            cursor -= 1
        if not run:
            continue

        standard = sum(slots[k].minutes() for k in run)
        give = min(short, standard - styling_minutes_for(standard, req.hours))
        if give <= 0:
            continue

        # Trim from the block nearest the shoot backwards: that is the one whose
        # end can move without touching anything else.
        left = give
        trimmed: dict[int, TimeSlot] = {}
        dropped: set[int] = set()
        for k in run:
            if left <= 0:
                break
            take = min(left, slots[k].minutes())
            left -= take
            left_minutes = slots[k].minutes() - take
            if left_minutes <= 0:
                dropped.add(k)
            else:
                trimmed[k] = replace(
                    slots[k],
                    end=fmt_minutes(to_minutes(slots[k].start) + left_minutes),
                    notes=(
                        f"{slots[k].notes}；妆造压缩为 {standard - give} 分钟"
                        f"（标准 {standard} 分钟），腾给拍摄"
                    ).strip("；"),
                )

        kept = [trimmed.get(k, s) for k, s in enumerate(slots) if k not in dropped]
        shoot_at = index - sum(1 for k in dropped if k < index)
        shoot = kept[shoot_at]
        kept[shoot_at] = replace(
            shoot,
            start=fmt_minutes(to_minutes(shoot.start) - give),
            label=_restate_duration(shoot.label, shoot.minutes(), shoot.minutes() + give),
            # The model's own prose often says "only five minutes left for the
            # shoot". Leaving that sentence next to a 30-minute block would make
            # the plan contradict itself, so the correction is written down.
            notes=(
                f"{shoot.notes}；该块已由 {shoot.minutes()} 分钟补足为 "
                f"{shoot.minutes() + give} 分钟"
            ).strip("；"),
        )
        # Keep the styling summary in step with the block, otherwise the plan
        # shows "100 minutes of styling" above an 90-minute block.
        styling = dict(plan.styling or {})
        if styling.get("makeup_minutes") is not None:
            styling["makeup_minutes"] = max(
                0, int(styling["makeup_minutes"]) - give
            )
        return replace(plan, slots=kept, styling=styling)
    return None
