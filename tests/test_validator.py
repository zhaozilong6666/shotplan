"""One test per rule. If a rule stops firing, these tests fail loudly."""

from __future__ import annotations

import copy
from math import fsum

from shotplan.agents.validator import SCORE_DECIMALS, validate
from shotplan.domain.constraints import SCORE_WEIGHTS
from shotplan.domain.models import PlanRequest


def codes(plan, brief, tools):
    return {issue.code for issue in validate(plan, brief, tools).issues}


def test_score_weights_sum_to_one():
    assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9


def test_perfect_plan_has_no_issues_and_full_score(valid_plan, brief, tools):
    report = validate(valid_plan, brief, tools)
    assert report.hard_pass is True
    assert report.issues == []
    assert report.score == 1.0  # exact, not 1.0000000000000002, on every Python version
    assert set(report.scores) == set(SCORE_WEIGHTS)


def test_scores_are_fixed_precision_and_use_exact_summation(valid_plan, brief, tools):
    """Python 3.12 changed sum() to compensated summation; scores must not drift.

    See ADR 0006. CI runs 3.10-3.13, so a float tail in the score breaks the
    eval gate on older interpreters even though the plan is identical.
    """
    report = validate(valid_plan, brief, tools)
    assert report.score == round(report.score, SCORE_DECIMALS)
    assert all(v == round(v, SCORE_DECIMALS) for v in report.scores.values())
    assert report.score == round(
        fsum(SCORE_WEIGHTS[k] * v for k, v in report.scores.items()), SCORE_DECIMALS
    )


def test_time_overlap_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[0].end = "16:50"  # overlaps the 15:26-15:46 travel block
    report = validate(plan, brief, tools)
    assert "TIME_OVERLAP" in codes(plan, brief, tools)
    assert report.hard_pass is False


def test_time_overrun_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[2].end = "20:00"
    assert "TIME_OVERRUN" in codes(plan, brief, tools)


def test_missing_activity_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots = plan.slots[1:]
    assert "MISSING_ACTIVITY" in codes(plan, brief, tools)


def test_makeup_order_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    makeup = plan.slots.pop(0)
    makeup.start, makeup.end = "18:00", "18:30"
    plan.slots.append(makeup)
    assert "MAKEUP_ORDER" in codes(plan, brief, tools)


def test_transfer_buffer_is_hard(valid_plan, brief, tools):
    from shotplan.domain.models import TimeSlot

    plan = copy.deepcopy(valid_plan)
    plan.slots[2].end = "16:20"
    plan.slots.append(
        TimeSlot("16:20", "17:40", "shoot", "qinglong_temple", "第二组", 0, [], "notes")
    )
    assert "TRANSFER_BUFFER" in codes(plan, brief, tools)


def test_budget_exceeded_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.total_cost_cny = 9999
    assert "BUDGET_EXCEEDED" in codes(plan, brief, tools)


def test_budget_uses_slot_cost_even_if_declared_total_is_low(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[0].cost_cny = 5000
    plan.total_cost_cny = 100
    assert "BUDGET_EXCEEDED" in codes(plan, brief, tools)


def test_unknown_location_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[2].location_id = "invented_palace"
    assert "UNKNOWN_LOCATION" in codes(plan, brief, tools)


def test_empty_travel_location_is_allowed(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[1].location_id = "-"
    assert "UNKNOWN_LOCATION" not in codes(plan, brief, tools)


def test_soft_rules_fire(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.alternatives = []
    plan.source_refs = []
    plan.slots[2].end = "15:40"
    plan.styling["style_id"] = "weijin"
    found = codes(plan, brief, tools)
    assert {"SHOOT_TOO_SHORT", "NO_ALTERNATIVE", "NO_SOURCE_REF",
            "STYLE_MISMATCH", "LIGHT_MISUSE"} <= found


def test_soft_issues_do_not_break_hard_pass(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.alternatives = []
    report = validate(plan, brief, tools)
    assert report.hard_pass is True
    assert report.score < 1.0


def test_missing_required_location_is_hard(valid_plan, tools):
    brief = PlanRequest(must_include=["qinglong_temple"])
    assert "MISSING_REQUIRED_LOCATION" in codes(valid_plan, brief, tools)


def test_required_location_satisfied_by_a_shoot_block(valid_plan, tools):
    brief = PlanRequest(must_include=["qinglong_temple"])
    plan = copy.deepcopy(valid_plan)
    plan.slots[2].location_id = "qinglong_temple"
    assert "MISSING_REQUIRED_LOCATION" not in codes(plan, brief, tools)


def test_avoided_location_is_hard(valid_plan, tools):
    brief = PlanRequest(avoid=["city_wall"])
    report = validate(valid_plan, brief, tools)
    assert "AVOIDED_LOCATION" in {issue.code for issue in report.issues}
    assert report.hard_pass is False


def test_unused_avoid_entry_is_not_an_issue(valid_plan, tools):
    brief = PlanRequest(avoid=["qinglong_temple"])
    assert "AVOIDED_LOCATION" not in codes(valid_plan, brief, tools)


def test_unknown_source_ref_is_hard(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.source_refs = ["locations.json#invented_palace", "styles.json#tang"]
    report = validate(plan, brief, tools)
    assert "UNKNOWN_SOURCE_REF" in {issue.code for issue in report.issues}
    assert report.hard_pass is False


def test_valid_source_refs_are_accepted(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.source_refs = ["locations.json#city_wall", "styles.json#tang"]
    assert "UNKNOWN_SOURCE_REF" not in codes(plan, brief, tools)


def test_score_degrades_with_each_hard_issue(valid_plan, brief, tools):
    clean = validate(valid_plan, brief, tools)
    one = copy.deepcopy(valid_plan)
    one.total_cost_cny = 9999
    two = copy.deepcopy(one)
    two.slots[2].location_id = "invented_palace"
    assert validate(two, brief, tools).score < validate(one, brief, tools).score < clean.score


def test_a_stated_departure_time_is_checked_not_just_recorded(valid_plan, brief, tools):
    """「赶17:00的高铁」 must do something: the plan ends at 18:26."""
    brief = copy.deepcopy(brief)
    brief.preferences = ["17:00前结束"]
    report = validate(valid_plan, brief, tools)
    found = [i for i in report.issues if i.code == "END_AFTER_DEADLINE"]
    assert len(found) == 1
    assert found[0].severity == "hard", "missing a train is not deliverable"
    assert report.hard_pass is False
    assert found[0].expected == "<= 17:00"


def test_a_deadline_the_plan_already_meets_raises_nothing(valid_plan, brief, tools):
    brief = copy.deepcopy(brief)
    brief.preferences = ["19:00前结束"]
    assert "END_AFTER_DEADLINE" not in codes(valid_plan, brief, tools)


def test_the_deadline_is_opt_in(valid_plan, brief, tools):
    assert "END_AFTER_DEADLINE" not in codes(valid_plan, brief, tools)


def test_deadline_parsing_ignores_text_that_only_looks_like_one():
    from shotplan.domain.constraints import deadline_minutes

    assert deadline_minutes(["17:00前结束"]) == 17 * 60
    assert deadline_minutes(["喜欢 17:00 的光线"]) is None
    assert deadline_minutes(["25:00前结束"]) is None
    assert deadline_minutes([]) is None
