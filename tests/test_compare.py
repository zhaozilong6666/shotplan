"""A comparison must be three real, different plans -- not three labels."""

from __future__ import annotations

from fastapi.testclient import TestClient

from shotplan.api import app
from shotplan.compare import compare_strategies
from shotplan.config import Settings
from shotplan.domain.models import PlanRequest

CFG = Settings(provider="mock", max_repairs=0)


def _brief(**overrides) -> PlanRequest:
    values = dict(
        city="xian", date="2026-10-04", hours=6, budget_cny=1500,
        styles=["tang"], preferences=["人少"],
    )
    values.update(overrides)
    return PlanRequest(**values)


def test_three_strategies_change_the_request_in_observable_ways():
    data = compare_strategies(_brief(), settings=CFG)
    by_id = {s["id"]: s for s in data["strategies"]}
    assert set(by_id) == {"showcase", "budget", "easy"}
    assert by_id["showcase"]["request"]["hours"] == 7
    assert by_id["budget"]["request"]["budget_cny"] < 1500
    assert by_id["easy"]["request"]["hours"] == 4
    assert by_id["easy"]["request"]["hours"] != by_id["budget"]["request"]["hours"]
    assert by_id["budget"]["request"]["avoid"], "budget must exclude expensive stops"
    assert by_id["easy"]["request"]["avoid"], "easy must exclude distant stops"
    assert by_id["budget"]["request"]["avoid"] != by_id["easy"]["request"]["avoid"]


def test_every_strategy_runs_the_real_pipeline_and_is_measured():
    data = compare_strategies(_brief(), settings=CFG)
    for s in data["strategies"]:
        m = s["metrics"]
        assert m["status"] == "ok" and m["hard_pass"] is True
        assert 0 < m["score"] <= 1
        assert set(m["scores"]) >= {"constraints", "budget", "light"}
        assert m["slots"] and m["shoot_count"] > 0
        assert m["plan_id"]
        assert isinstance(s["tradeoffs"], list) and s["tradeoffs"]
        assert "identical_to" in s


def test_strategies_produce_distinct_plans_and_duplicates_are_flagged():
    data = compare_strategies(_brief(), settings=CFG)
    assert data["distinct_plans"] == 3, "three labels must mean three plans"
    assert all(not s["identical_to"] for s in data["strategies"])
    fingerprints = {
        s["id"]: tuple(
            (x["location_id"], x["activity"], x["start"], x["end"])
            for x in s["metrics"]["slots"]
        )
        for s in data["strategies"]
    }
    assert len(set(fingerprints.values())) == data["distinct_plans"]
    for s in data["strategies"]:
        expected = [
            other for other, fp in fingerprints.items()
            if other != s["id"] and fp == fingerprints[s["id"]]
        ]
        assert sorted(s["identical_to"]) == sorted(expected)


def test_recommendation_follows_the_users_own_words():
    cheap = compare_strategies(_brief(preferences=["控制预算"]), settings=CFG)
    assert cheap["recommendation"]["id"] == "budget"
    assert cheap["recommendation"]["basis"] == "preference"
    assert "控制预算" in cheap["recommendation"]["evidence"]

    easy = compare_strategies(_brief(preferences=["别太累"]), settings=CFG)
    assert easy["recommendation"]["id"] == "easy"

    neutral = compare_strategies(_brief(preferences=[]), settings=CFG)
    assert neutral["recommendation"]["basis"] == "score"


def test_tradeoffs_are_computed_differences_not_marketing():
    data = compare_strategies(_brief(), settings=CFG)
    for s in data["strategies"][1:]:
        assert any(any(ch.isdigit() for ch in t) for t in s["tradeoffs"]), s["tradeoffs"]


def test_api_compare_endpoint_contract():
    with TestClient(app) as client:
        response = client.post("/api/compare", json={
            "request": {
                "city": "xian", "date": "2026-10-04", "hours": 6,
                "budget_cny": 1500, "styles": ["tang"],
            }
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["strategies"]) == 3
        assert data["recommendation"]["id"] in {"showcase", "budget", "easy"}
        bad = client.post("/api/compare", json={"request": "nope"})
        assert bad.status_code == 422
