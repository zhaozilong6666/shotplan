"""A holdout for the prompt router: fresh cases, no leakage from the set that
suggested the rule. Validating the router on S01-S12 would be selection bias.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evals.run_eval import load_cases

from shotplan.agents import MockProvider
from shotplan.agents.openai_compatible import _wants_light_aware_prompt
from shotplan.domain.feasibility import check_feasibility
from shotplan.domain.models import PlanRequest
from shotplan.knowledge.tools import get_tools
from shotplan.pipeline import run_plan

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "evals" / "router_holdout"


@pytest.fixture(scope="module")
def cases():
    return load_cases(HOLDOUT)


def test_sixteen_fresh_cases_split_eight_eight(cases):
    assert len(cases) == 16
    assert sum(c["group"] == "light" for c in cases) == 8
    assert sum(c["group"] == "ordinary" for c in cases) == 8
    old = load_cases(ROOT / "evals" / "redteam_soft")
    assert not ({c["id"] for c in cases} & {c["id"] for c in old})


def test_every_case_is_feasible_and_solvable(cases):
    tools = get_tools()
    for case in cases:
        request = PlanRequest(**case["request"])
        assert check_feasibility(request, tools).feasible, f"{case['id']} rejected by preflight"
        result = run_plan(request, provider=MockProvider("good"))
        assert result.validation.hard_pass, f"{case['id']} impossible for the good planner"


def test_router_fires_on_the_light_half_only(cases):
    fired = [c["id"] for c in cases if _wants_light_aware_prompt(PlanRequest(**c["request"]))]
    assert fired == [c["id"] for c in cases if c["group"] == "light"]
