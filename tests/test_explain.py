"""Explanations must cite the knowledge base, not invent a story."""

from __future__ import annotations

import copy

from shotplan.agents import MockProvider
from shotplan.domain.models import PlanRequest
from shotplan.explain import explain_plan
from shotplan.pipeline import run_plan


def test_every_shoot_block_gets_an_entry(valid_plan, brief, tools):
    entries = explain_plan(valid_plan, brief, tools)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["location_id"] == "city_wall"
    assert entry["start"] == "16:26"
    assert entry["end"] == "18:26"
    joined = " ".join(entry["reasons"])
    # Style evidence comes from styles.json + locations.json.
    assert "唐制" in joined and "tang" in joined
    # Light evidence must be the same solar window the validator used.
    assert "16:26–18:26" in joined and "黄金窗口" in joined
    assert "全程落在窗口内" in joined


def test_non_shoot_blocks_are_not_explained(valid_plan, brief, tools):
    entries = explain_plan(valid_plan, brief, tools)
    assert [entry["slot_index"] for entry in entries] == [2]


def test_must_include_is_named_as_the_reason(valid_plan, brief, tools):
    request = PlanRequest(
        city="xian",
        date="2026-10-04",
        hours=5,
        budget_cny=1500,
        styles=["tang"],
        must_include=["city_wall"],
    )
    reasons = " ".join(explain_plan(valid_plan, request, tools)[0]["reasons"])
    assert "点名要求" in reasons


def test_partial_overlap_is_reported_honestly(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[2].start = "17:30"
    plan.slots[2].end = "18:56"  # 56 minutes inside, 30 outside
    reasons = " ".join(explain_plan(plan, brief, tools)[0]["reasons"])
    assert "56 分钟落在黄金窗口" in reasons
    assert "全程落在窗口内" not in reasons


def test_unknown_location_is_flagged_instead_of_raising(valid_plan, brief, tools):
    plan = copy.deepcopy(valid_plan)
    plan.slots[2].location_id = "invented_palace"
    entry = explain_plan(plan, brief, tools)[0]
    assert "不在当前知识库" in entry["reasons"][0]


def test_pipeline_result_carries_explanations():
    request = PlanRequest(city="xian", date="2026-10-04", hours=5, budget_cny=1500)
    result = run_plan(request, provider=MockProvider("good"), max_repairs=0)
    assert result.status == "ok"
    assert result.explanations
    assert result.to_dict()["explanations"] == result.explanations
    assert all(entry["reasons"] for entry in result.explanations)
