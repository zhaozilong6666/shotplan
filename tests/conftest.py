"""Shared fixtures. Also makes `pytest` work without installing the package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

import shotplan.config as config
from shotplan.domain.models import Plan, PlanRequest, TimeSlot
from shotplan.knowledge.tools import get_tools


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch):
    """Stop a developer's local `.env` or shell exports from changing results.

    Without this, `api_key_set` flipped from False to True the moment someone
    pointed the project at a real provider, and the unit suite failed for a
    reason that had nothing to do with the code under test.
    """
    monkeypatch.setenv("SHOTPLAN_SKIP_DOTENV", "1")
    monkeypatch.setattr(config, "_DOTENV", {})
    for name in [key for key in os.environ if key.startswith("SHOTPLAN_")]:
        if name != "SHOTPLAN_SKIP_DOTENV":
            monkeypatch.delenv(name, raising=False)


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
            # Xi'an on 2026-10-04: golden window is 16:26-18:26. Keep the
            # fixture perfect against the real solar window, not a stale table.
            TimeSlot("13:26", "15:26", "makeup", "indoor_studio", "唐制妆造", 400,
                     [style_ref], "含发型与着装"),
            TimeSlot("15:26", "15:46", "travel", "city_wall", "转场", 50,
                     [wall_ref], "含补妆与器材搬运"),
            TimeSlot("16:26", "18:26", "shoot", "city_wall", "城墙拍摄", 120,
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
