"""Code owns the minimum shootable block length."""

from __future__ import annotations

from shotplan.domain.constraints import styling_minutes_for
from shotplan.domain.models import Plan, PlanRequest, TimeSlot, to_minutes
from shotplan.timeline import compress_styling_for_shoots, repair_short_shoots


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


def test_the_floor_scales_with_the_booking():
    """A 1.5h session cannot hold a 60-minute block; the rule must know that."""
    from shotplan.domain.constraints import min_shoot_block_minutes as floor

    assert floor(1.5) == 30      # 90 min booking -> floor, not 60
    assert floor(2.0) == 30
    assert floor(2.5) == 37
    assert floor(3.0) == 45
    assert floor(4.0) == 60      # from here the ceiling applies
    assert floor(7.0) == 60


def test_styling_cap_scales_with_the_booking():
    """C46: an 85-minute styling block cannot fit a 1.5h booking."""
    assert styling_minutes_for(85, 1.5) == 60   # 31 min proportional -> floor
    assert styling_minutes_for(85, 2.0) == 60   # 42 -> floor
    assert styling_minutes_for(85, 3.0) == 63   # 180*0.35 rounds to 63
    assert styling_minutes_for(85, 4.0) == 84   # 84 -> share applies
    assert styling_minutes_for(85, 5.0) == 85   # cap exceeds the style's standard
    assert styling_minutes_for(40, 1.5) == 40   # never longer than standard


def test_a_physically_short_block_is_not_flagged():
    """The 8 real-model failures were mostly 1.5-3h briefs, i.e. impossible asks."""
    from shotplan.agents import validate

    # 1.5h session: makeup 90 + travel 20 leaves 40 for the shoot.
    slots = [
        TimeSlot("08:00", "09:30", "makeup", "indoor_studio", "妆造", 400),
        TimeSlot("09:30", "09:50", "travel", "city_wall", "转场", 50),
        TimeSlot("09:50", "10:30", "shoot", "city_wall", "城墙", 120),
    ]
    req = _req(hours=1.5)
    codes = [i.code for i in validate(_plan(slots), req, None).issues]
    assert "SHOOT_TOO_SHORT" not in codes

    # With 7 hours the same 40-minute block really is a planning mistake.
    codes = [i.code for i in validate(_plan(slots), _req(hours=7), None).issues]
    assert "SHOOT_TOO_SHORT" in codes


def _tight_slots(makeup_minutes: int = 85, shoot_minutes: int = 5) -> list[TimeSlot]:
    """The real C46 shape: 1.5h booking, 85 min styling, 5 min of shooting."""
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
    start = 14 * 60 + 47
    makeup_end = start + makeup_minutes
    shoot_end = makeup_end + shoot_minutes
    return [
        TimeSlot(fmt(start), fmt(makeup_end), "makeup", "lijing_gate", "妆造", 400),
        TimeSlot(fmt(makeup_end), fmt(shoot_end), "shoot", "lijing_gate", "拍摄", 120),
    ]


def test_oversized_styling_gives_time_to_the_short_shoot():
    req = _req(hours=1.5)
    plan = _plan(_tight_slots())
    fixed = compress_styling_for_shoots(plan, req)
    assert fixed is not None
    assert fixed.slots[0].minutes() == 60, "styling capped for a 90-minute booking"
    assert fixed.slots[1].minutes() == 30, "the shoot gets the floor, not five minutes"
    # Nothing after the shoot moved, and the original plan is untouched.
    assert fixed.slots[1].end == plan.slots[1].end
    assert plan.slots[0].minutes() == 85
    # The trade must be visible to the client, not silent.
    assert "压缩为 60 分钟" in fixed.slots[0].notes


def test_styling_is_not_cut_when_the_shoot_already_fits():
    """7h booking: the floor is 60 and the 85-minute styling is not the problem."""
    req = _req(hours=7)
    plan = _plan(_tight_slots(makeup_minutes=85, shoot_minutes=40))
    assert compress_styling_for_shoots(plan, req) is None


def test_a_transfer_between_styling_and_shoot_blocks_the_repair():
    """Only an immediately adjacent pair is safe; otherwise later slots would move."""
    req = _req(hours=1.5)
    slots = _tight_slots()
    slots.insert(1, TimeSlot("16:12", "16:32", "travel", "lijing_gate", "转场", 50))
    assert compress_styling_for_shoots(_plan(slots), req) is None


def test_split_styling_blocks_are_treated_as_one_run():
    """The model writes "60 min makeup" + "25 min costume"; both are styling."""
    req = _req(hours=1.5)
    slots = [
        TimeSlot("09:00", "10:00", "makeup", "lijing_gate", "妆面", 300),
        TimeSlot("10:00", "10:25", "makeup", "lijing_gate", "换装", 100),
        TimeSlot("10:25", "10:30", "shoot", "lijing_gate", "快拍", 120),
    ]
    fixed = compress_styling_for_shoots(_plan(slots), req)
    assert fixed is not None
    assert [s.activity for s in fixed.slots] == ["makeup", "shoot"]
    assert fixed.slots[0].minutes() == 60
    assert fixed.slots[1].minutes() == 30
    assert fixed.slots[1].end == "10:30", "the shoot keeps its end"


def test_the_shoots_own_prose_is_corrected_not_left_contradicting_itself():
    """The model writes "only 5 minutes left"; after the repair it is 30."""
    req = _req(hours=1.5)
    slots = [
        TimeSlot("09:00", "10:00", "makeup", "lijing_gate", "妆面", 300),
        TimeSlot("10:00", "10:25", "makeup", "lijing_gate", "换装", 100),
        TimeSlot("10:25", "10:30", "shoot", "lijing_gate", "仅能安排5分钟快拍", 120),
    ]
    fixed = compress_styling_for_shoots(_plan(slots), req)
    assert fixed is not None
    # The label said "5 分钟"; it must not survive next to a 30-minute block.
    assert "5分钟" not in fixed.slots[1].label
    assert "30 分钟" in fixed.slots[1].label
    assert "补足为 30 分钟" in fixed.slots[1].notes, "and the change is recorded"


def test_the_styling_summary_follows_the_compressed_block():
    req = _req(hours=1.5)
    plan = _plan(_tight_slots())
    plan = Plan(city="xian", date="2026-10-04", slots=plan.slots, total_cost_cny=600,
                styling={"style_id": "republic", "makeup_minutes": 60, "costume_minutes": 25})
    fixed = compress_styling_for_shoots(plan, req)
    assert fixed is not None
    assert fixed.styling["makeup_minutes"] == 35, "60 - 25 given to the shoot"
    assert fixed.styling["costume_minutes"] == 25, "costume duration is untouched"
