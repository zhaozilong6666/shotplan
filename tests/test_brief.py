"""The system must understand intent honestly and never propose a broken fix."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from shotplan.api import app
from shotplan.brief import understand
from shotplan.domain.feasibility import check_feasibility
from shotplan.domain.models import PlanRequest
from shotplan.knowledge.tools import get_tools
from shotplan.negotiation import negotiate

TODAY = dt.date(2026, 11, 15)


def test_explicit_brief_is_parsed_without_silent_guesses():
    u = understand(
        "10月4号苏州拍宋制，两个人，预算2000元以内，想要园林和小众一点",
        today=TODAY,
    )
    r = u.request
    assert r.city == "suzhou" and r.date == "2027-10-04"
    assert r.budget_cny == 2000 and r.party_size == 2
    assert r.styles == ["song"]
    assert "小众" in r.preferences
    by_field = {a.field: a for a in u.assumptions}
    for key in ("city", "date", "budget_cny", "styles", "party_size"):
        assert by_field[key].source == "stated", (key, by_field[key])
    assert not u.clarifications


def test_aesthetic_words_produce_a_low_confidence_inference_not_a_fake_fact():
    u = understand("想拍点温柔的氛围感照片", today=TODAY)
    by_field = {a.field: a for a in u.assumptions}
    assert u.request.styles == ["song"]
    assert by_field["styles"].source == "inferred"
    assert by_field["styles"].confidence < 0.7
    assert "温柔" in by_field["styles"].evidence
    assert by_field["atmosphere"].source == "inferred"
    assert any(q["why"] == "关键条件几乎全是默认值" for q in u.clarifications)


def test_inferred_time_becomes_a_question_not_a_hidden_decision():
    u = understand("国庆去西安拍唐风，最好上午收工", today=TODAY)
    assert u.request.hours == 3.0
    assert any("推断" in q["why"] or "推断" in q["question"] for q in u.clarifications)


def test_year_is_never_misread_as_a_budget():
    u = understand("2026年11月去西安拍唐风", today=TODAY)
    assert u.request.budget_cny == 1500
    assert next(a for a in u.assumptions if a.field == "budget_cny").source == "default"


def test_understand_is_deterministic():
    text = "国庆去西安拍唐风，预算一千五，人少一点"
    assert understand(text, today=TODAY).to_dict() == understand(text, today=TODAY).to_dict()


def test_avoid_maps_a_human_place_name_to_a_location_id():
    u = understand("去西安，别去大唐芙蓉园", today=TODAY)
    assert "datang_furong_garden" in u.request.avoid


def test_every_negotiation_is_verified_feasible():
    impossible = PlanRequest(city="xian", hours=1.0, budget_cny=100)
    assert not check_feasibility(impossible, get_tools()).feasible
    options = negotiate(impossible)
    assert len(options) >= 2, "give the user a choice, not a single decree"
    for option in options:
        assert option.verified
        assert check_feasibility(option.request, get_tools()).feasible, option.label


def test_negotiate_returns_nothing_when_the_brief_is_already_feasible():
    assert negotiate(PlanRequest()) == []


def test_api_understand_endpoint_contract():
    with TestClient(app) as client:
        response = client.post("/api/understand", json={
            "text": "去西安，1小时，预算100元", "today": "2026-11-15",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["feasibility"]["feasible"] is False
        assert data["negotiations"]
        assert all(o["verified"] for o in data["negotiations"])
        assert data["assumptions"] and data["raw_text"]

        bad = client.post("/api/understand", json={"text": "  "})
        assert bad.status_code == 422
        bad_date = client.post("/api/understand", json={"text": "去西安", "today": "oops"})
        assert bad_date.status_code == 422
