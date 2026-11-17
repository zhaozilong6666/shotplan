"""Follow-ups must change only what the user asked for, and say so."""

from __future__ import annotations

from fastapi.testclient import TestClient

from shotplan.api import app
from shotplan.domain.models import PlanRequest
from shotplan.followup import apply_followup


def _previous(**overrides) -> PlanRequest:
    values = dict(
        city="xian", date="2026-10-04", hours=2, budget_cny=400,
        styles=["tang"], preferences=["人少"],
    )
    values.update(overrides)
    return PlanRequest(**values)


def test_more_budget_adds_a_fixed_step_and_preserves_everything_else():
    previous = _previous()
    result = apply_followup(previous, "预算多一点")
    assert result.recognized is True
    assert result.request.budget_cny == 700
    assert result.request.hours == previous.hours
    assert result.request.city == previous.city
    assert result.request.request_id == previous.request_id
    assert "预算 400 → 700 元" in result.changes


def test_more_time_adds_an_hour_and_caps_at_twelve():
    result = apply_followup(_previous(hours=2), "时间再多一点")
    assert result.request.hours == 3
    capped = apply_followup(_previous(hours=11.5), "时长长一点")
    assert capped.request.hours == 12


def test_relax_and_quiet_words_only_add_preferences():
    relaxed = apply_followup(_previous(), "再松一点")
    assert relaxed.recognized is True
    assert {"轻松", "少转场"} <= set(relaxed.request.preferences)
    assert relaxed.request.budget_cny == 400 and relaxed.request.hours == 2

    quiet = apply_followup(_previous(preferences=[]), "人少一点")
    assert {"人少", "安静"} <= set(quiet.request.preferences)


def test_city_change_comes_from_the_knowledge_base():
    result = apply_followup(_previous(), "换到杭州，预算多一点")
    assert result.request.city == "hangzhou"
    assert result.request.budget_cny == 700
    assert any("城市 xian → hangzhou" in change for change in result.changes)


def test_unknown_wording_is_not_guessed():
    previous = _previous()
    result = apply_followup(previous, "把天空调成粉红色")
    assert result.recognized is False
    assert result.changes == []
    assert result.request.to_dict() == previous.normalized().to_dict()
    assert "没识别出" in result.message


def test_empty_text_is_not_recognised():
    result = apply_followup(_previous(), "   ")
    assert result.recognized is False


def test_followup_is_deterministic():
    previous = _previous()
    first = apply_followup(previous, "换到杭州，再松一点").to_dict()
    second = apply_followup(previous, "换到杭州，再松一点").to_dict()
    assert first == second


def test_api_followup_contract():
    with TestClient(app) as client:
        response = client.post("/api/followup", json={
            "previous": _previous().to_dict(),
            "text": "预算多一点",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["recognized"] is True
        assert data["request"]["budget_cny"] == 700
        assert data["changes"]

        bad = client.post("/api/followup", json={"previous": {}, "text": "  "})
        assert bad.status_code == 422
        no_previous = client.post("/api/followup", json={"text": "预算多一点"})
        assert no_previous.status_code == 422
