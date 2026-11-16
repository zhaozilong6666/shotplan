"""Code owns the minimum shootable block length."""

from __future__ import annotations

from shotplan.domain.models import Plan, PlanRequest, TimeSlot, to_minutes
from shotplan.timeline import repair_short_shoots


def _req(hours: float = 6) -> PlanRequest:
    return PlanRequest(city="xian", date="2026-10-04", hours=hours,
                       budget_cny=1500, styles=["tang"])


def _plan(slots: list[TimeSlot]) -> Plan:
    return Plan(city="xian", date="2026-10-04", slots=slots, total_cost_cny=600)


def _slots(trailing_minutes: int = 40) -> list[TimeSlot]:
    start = 8 * 60
    makeup_end = start + 90
    travel_end = makeup_end + 20
    shoot_end = travel_end + trailing_minutes
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
    return [
        TimeSlot(fmt(start), fmt(makeup_end), "makeup", "indoor_studio", "妆造", 400),
        TimeSlot(fmt(makeup_end), fmt(travel_end), "travel", "city_wall", "转场", 50),
        TimeSlot(fmt(travel_end), fmt(shoot_end), "shoot", "city_wall", "城墙拍摄", 120),
    ]


def test_a_short_trailing_block_is_extended_into_the_unused_tail():
    req = _req(hours=6)
    plan = _plan(_slots(40))
    fixed = repair_short_shoots(plan, req)
    assert fixed is not None
    last = fixed.slots[-1]
    assert last.minutes() >= 60
    session_end = to_minutes(fixed.slots[0].start) + int(req.hours * 60)
    assert to_minutes(last.end) == session_end
    # the original plan is untouched
    assert plan.slots[-1].minutes() == 40


def test_an_adequate_block_is_left_alone():
    assert repair_short_shoots(_plan(_slots(75)), _req()) is None


def test_a_block_in_the_middle_is_not_stretched():
    """Stretching mid-timeline would push everything after it into an overlap."""
    req = _req()
    slots = _slots(40) + [
        TimeSlot("11:10", "11:30", "travel", "indoor_studio", "返回", 0),
        TimeSlot("11:30", "11:45", "rest", "indoor_studio", "休整", 0),
    ]
    assert repair_short_shoots(_plan(slots), req) is None


def test_no_room_means_no_change():
    """A 1.5h session leaves nothing to give; the plan must stay as it is."""
    req = _req(hours=1.5)
    assert repair_short_shoots(_plan(_slots(40)), req) is None


def test_the_extended_block_never_exceeds_the_booked_window():
    req = _req(hours=6)
    fixed = repair_short_shoots(_plan(_slots(40)), req)
    assert fixed is not None
    assert to_minutes(fixed.slots[-1].end) <= to_minutes(fixed.slots[0].start) + int(req.hours * 60)
