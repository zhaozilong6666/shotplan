"""One test per rule. If a rule stops firing, these tests fail loudly."""

from __future__ import annotations

import copy
from math import fsum

from shotplan.agents.validator import SCORE_DECIMALS, validate
from shotplan.domain.constraints import SCORE_WEIGHTS


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
    plan.slots[0].end = "15:30"
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


def test_score_degrades_with_each_hard_issue(valid_plan, brief, tools):
    clean = validate(valid_plan, brief, tools)
    one = copy.deepcopy(valid_plan)
    one.total_cost_cny = 9999
    two = copy.deepcopy(one)
    two.slots[2].location_id = "invented_palace"
    assert validate(two, brief, tools).score < validate(one, brief, tools).score < clean.score
