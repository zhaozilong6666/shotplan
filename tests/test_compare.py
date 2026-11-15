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


def test_strategies_never_change_the_users_hard_constraints():
    """A comparison that quietly edits the brief is a lie, not a choice."""
    data = compare_strategies(_brief(), settings=CFG)
    for s in data["strategies"]:
        assert s["request"]["hours"] == 6, s["id"]
        assert s["request"]["budget_cny"] == 1500, s["id"]
        assert s["request"]["city"] == "xian"
        assert s["within_original_constraints"] is True
        assert s["relaxations"] == []


def test_three_strategies_change_the_request_in_observable_ways():
    data = compare_strategies(_brief(), settings=CFG)
    by_id = {s["id"]: s for s in data["strategies"]}
    assert set(by_id) == {"showcase", "budget", "easy"}
    # Differences live in preferences and excluded candidates, not constraints.
    assert "黄金光线" in by_id["showcase"]["request"]["preferences"]
    assert "控制预算" in by_id["budget"]["request"]["preferences"]
    assert "少转场" in by_id["easy"]["request"]["preferences"]
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
    # No strong preference: the strategies are free to diverge here. A strong
    # preference legitimately collapses them, which the next test covers.
    data = compare_strategies(_brief(preferences=[]), settings=CFG)
    assert data["distinct_plans"] >= 2, "at least one strategy must differ"
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


def test_a_strong_preference_can_collapse_the_three_into_one_and_says_so():
    """Convergence is a finding, not a bug -- but it must be stated."""
    data = compare_strategies(_brief(preferences=["人少", "安静"]), settings=CFG)
    if data["distinct_plans"] == 1:
        note = data["recommendation"].get("note", "")
        assert "稳健" in note or "重合" in note


def test_a_nearby_strategy_actually_picks_nearer_scenes():
    """'Less running around' must be visible in the selected scenes, not just the label."""
    data = compare_strategies(_brief(preferences=[]), settings=CFG)
    easy = next(s for s in data["strategies"] if s["id"] == "easy")
    showcase = next(s for s in data["strategies"] if s["id"] == "showcase")
    if easy["identical_to"]:
        assert data["recommendation"].get("note"), (
            "if the strategies converge, the result must say so"
        )
        return
    assert easy["metrics"]["scene_travel_minutes"] <= showcase["metrics"]["scene_travel_minutes"], (
        easy["metrics"]["scene_travel_minutes"], showcase["metrics"]["scene_travel_minutes"]
    )


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
        if s["identical_to"]:
            assert s["tradeoffs"] == ["与出片优先版在可量化指标上持平"]
        else:
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


def test_compare_endpoint_requires_the_api_key_like_plan_does():
    """An unauthenticated triple-run endpoint is a 3x cost amplifier."""
    from shotplan.api import app as api_app
    from shotplan.config import Settings as S

    cfg = S(provider="mock", api_key="secret-key")
    from shotplan.api import app_settings

    api_app.dependency_overrides[app_settings] = lambda: cfg
    try:
        with TestClient(api_app) as client:
            body = {"request": {"city": "xian", "hours": 6, "budget_cny": 1500,
                                "styles": ["tang"]}}
            anonymous = client.post("/api/compare", json=body)
            assert anonymous.status_code == 401, anonymous.text[:120]
            allowed = client.post("/api/compare", json=body,
                                  headers={"X-API-Key": "secret-key"})
            assert allowed.status_code == 200
            payload = allowed.json()
            assert payload["runs_executed"] == 3
            assert "cost_cny" in payload
    finally:
        api_app.dependency_overrides.clear()


def test_compare_counts_as_three_rate_limit_slots():
    from shotplan.api import _COSTLY_PATHS

    assert _COSTLY_PATHS["/api/compare"] == 3
    assert _COSTLY_PATHS["/api/plan"] == 1
