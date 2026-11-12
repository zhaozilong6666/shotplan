"""Shared fixtures. Also makes `pytest` work without installing the package."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

from shotplan.domain.models import Plan, PlanRequest, TimeSlot
from shotplan.knowledge.tools import get_tools


@pytest.fixture
def tools():
    return get_tools()


@pytest.fixture
def brief():
    return PlanRequest(
        city="xian",
        date="2026-10-04",
        hours=5,
        budget_cny=1500,
        styles=["tang"],
    )


@pytest.fixture
def valid_plan():
    """A plan that should pass every single rule with a perfect score."""
    style_ref = "styles.json#tang"
    wall_ref = "locations.json#city_wall"
    return Plan(
        request_id="req_test",
        city="xian",
        date="2026-10-04",
        slots=[
            TimeSlot("13:00", "15:00", "makeup", "indoor_studio", "唐制妆造", 400,
                     [style_ref], "含发型与着装"),
            TimeSlot("15:00", "15:20", "travel", "city_wall", "转场", 50,
                     [wall_ref], "含补妆与器材搬运"),
            TimeSlot("15:20", "18:00", "shoot", "city_wall", "城墙拍摄", 120,
                     [wall_ref], "傍晚黄金光线"),
        ],
        styling={"style_id": "tang", "name": "唐制"},
        total_cost_cny=570,
        alternatives=[{"trigger": "rain", "description": "改室内棚", "location_id": "indoor_studio"}],
        assumptions=["按秋季黄金光线估算"],
        source_refs=[style_ref, wall_ref],
        provider="test",
        prompt_version="test-v1",
    )
