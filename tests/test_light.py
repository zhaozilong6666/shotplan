"""The golden window is decided by code, and it must not override the brief."""

from __future__ import annotations

import pytest

from shotplan.agents import validate
from shotplan.agents.mock import MockProvider
from shotplan.domain.models import Plan, PlanRequest, TimeSlot, to_minutes
from shotplan.knowledge.tools import get_tools
from shotplan.light import align_light_window

KB = get_tools()


def _req(**kwargs) -> PlanRequest:
    base = dict(city="xian", date="2026-10-04", hours=6, budget_cny=1500, styles=["tang"])
    base.update(kwargs)
    return PlanRequest(**base)


def _morning_plan(req: PlanRequest) -> Plan:
    """A plausible plan whose only shoot sits at 09:50, well outside 16:00-18:00."""
    slots = [
        TimeSlot("08:00", "09:30", "makeup", "indoor_studio", "妆造", 400),
        TimeSlot("09:30", "09:50", "travel", "city_wall", "转场", 50),
        TimeSlot("09:50", "11:20", "shoot", "city_wall", "城墙拍摄", 120),
    ]
    return Plan(
        city=req.city, date=req.date, slots=slots, total_cost_cny=570,
        alternatives=[{"trigger": "rain", "description": "改室内", "location_id": "indoor_studio"}],
        source_refs=["styles.json#tang", "locations.json#city_wall"],
    )


def test_golden_window_is_where_the_knowledge_base_says_it_is():
    assert KB.light_window("2026-10-04") == ("16:00", "18:00")
    assert KB.light_window("2026-01-10") == ("15:00", "17:00")


def test_alignment_moves_the_trailing_shoot_into_the_window():
    req = _req()
    plan = _morning_plan(req)
    aligned = align_light_window(plan, req, KB)
    assert aligned is not None
    last = aligned.slots[-1]
    assert last.activity == "shoot"
    start, end = to_minutes(last.start), to_minutes(last.end)
    assert start >= to_minutes("16:00")
    assert end <= to_minutes("18:00")
    assert end - start >= 60, "the block must stay long enough to shoot"
    # the original object is untouched
    assert plan.slots[-1].start == "09:50"


def test_a_morning_finish_brief_is_never_pushed_into_the_evening():
    """An empty golden window is the correct answer when the user asked for noon."""
    req = _req(preferences=["上午收工", "人少"])
    plan = _morning_plan(req)
    assert align_light_window(plan, req, KB) is None


def test_already_aligned_plans_are_left_alone():
    req = _req()
    plan = _morning_plan(req)
    plan.slots[-1] = TimeSlot("16:10", "17:40", "shoot", "city_wall", "城墙拍摄", 120)
    assert align_light_window(plan, req, KB) is None


def test_alignment_never_breaks_the_booked_window():
    req = _req(hours=1.5)
    plan = _morning_plan(req)
    aligned = align_light_window(plan, req, KB)
    if aligned is not None:
        session_start = to_minutes(aligned.slots[0].start)
        assert to_minutes(aligned.slots[-1].end) <= session_start + int(req.hours * 60)


def test_the_validator_stops_flagging_morning_finish_briefs():
    morning = _morning_plan(_req(preferences=["上午收工"]))
    codes = [issue.code for issue in validate(morning, _req(preferences=["上午收工"]), KB).issues]
    assert "LIGHT_MISUSE" not in codes

    evening = _morning_plan(_req(preferences=["傍晚光线"]))
    codes = [issue.code for issue in validate(evening, _req(preferences=["傍晚光线"]), KB).issues]
    assert "LIGHT_MISUSE" in codes


def test_pipeline_repairs_the_light_without_asking_the_model_again():
    """End-to-end: a plan shifted to the morning is fixed by code, not by a retry."""
    from dataclasses import replace

    from shotplan.agents import parse_plan
    from shotplan.domain.models import fmt_minutes
    from shotplan.pipeline import _validate_with_light

    req = _req()
    generation = MockProvider("good").generate(req, KB.context_for(req))
    plan = parse_plan(generation.payload, req, "mock", "planner-v2")

    delta = 8 * 60 - to_minutes(plan.slots[0].start)
    morning = replace(
        plan,
        slots=[
            replace(
                slot,
                start=fmt_minutes(to_minutes(slot.start) + delta),
                end=fmt_minutes(to_minutes(slot.end) + delta),
            )
            for slot in plan.slots
        ],
    )
    window_start, window_end = (to_minutes(x) for x in KB.light_window(req.date))
    assert not any(
        to_minutes(s.start) < window_end and to_minutes(s.end) > window_start
        for s in morning.slots
        if s.activity == "shoot"
    ), "precondition: the shifted plan must miss the window"

    fixed, report, aligned = _validate_with_light(morning, req, KB)
    assert aligned is True
    assert "LIGHT_MISUSE" not in [issue.code for issue in report.issues]
    assert report.score >= validate(morning, req, KB).score
    assert len({s.activity for s in fixed.slots}) == len({s.activity for s in morning.slots})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
