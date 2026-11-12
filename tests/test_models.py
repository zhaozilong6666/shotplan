from dataclasses import replace

import pytest

from shotplan.domain.models import Plan, PlanRequest, TimeSlot, fmt_minutes, to_minutes


def test_time_parsing_roundtrip():
    assert to_minutes("09:05") == 545
    assert fmt_minutes(545) == "09:05"
    assert to_minutes("23:59") == 1439


@pytest.mark.parametrize("bad", ["", "9:5:0", "24:00", "12:60", "abc", None])
def test_time_parsing_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        to_minutes(bad)


def test_slot_minutes():
    assert TimeSlot("09:00", "10:30", "shoot", "x", "x").minutes() == 90


def test_request_normalization_clamps_and_lowercases():
    raw = PlanRequest(city="  XIAN ", hours=99, budget_cny=-5, party_size=0,
                      styles=[" TANG ", "", "song"])
    norm = raw.normalized()
    assert norm.city == "xian"
    assert norm.hours == 12.0
    assert norm.budget_cny == 0
    assert norm.party_size == 1
    assert norm.styles == ["tang", "song"]
    assert norm.request_id == raw.request_id


def test_request_from_dict_ignores_unknown_fields():
    req = PlanRequest.from_dict({"city": "XIAN", "hours": 4, "unexpected": 123})
    assert req.city == "xian" and req.hours == 4.0


def test_plan_from_dict_rebuilds_nested_slots():
    plan = Plan(slots=[TimeSlot("09:00", "10:00", "shoot", "city_wall", "x")])
    rebuilt = Plan.from_dict(plan.to_dict())
    assert isinstance(rebuilt.slots[0], TimeSlot)
    assert rebuilt.slots[0].location_id == "city_wall"


def test_plan_from_dict_tolerates_missing_optional_fields():
    plan = Plan.from_dict({"slots": [{"start": "09:00", "end": "10:00",
                                      "activity": "shoot", "location_id": "city_wall"}]})
    assert plan.slots[0].label == "shoot"
    assert plan.total_cost_cny == 0


def test_replace_keeps_request_id():
    req = PlanRequest()
    changed = replace(req, hours=8)
    assert changed.request_id == req.request_id


def test_validation_report_shape():
    from shotplan.domain.models import ValidationIssue, ValidationReport

    report = ValidationReport(issues=[ValidationIssue("X", "hard", "msg")],
                              scores={"constraints": 0.75}, score=0.75, hard_pass=False)
    assert report.hard_issues[0].code == "X"
    assert report.soft_issues == []
    assert report.to_dict()["hard_pass"] is False
