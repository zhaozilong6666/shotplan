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


# --- amounts: the layer that used to be able to add only one fixed step --------


def test_a_stated_amount_is_arithmetic_not_a_preference():
    result = apply_followup(_previous(), "能不能便宜 300")
    assert result.recognized is True
    assert result.request.budget_cny == 100
    # The change names the arithmetic, so the client can check it.
    assert "减 300" in result.changes[0]


def test_an_absolute_budget_replaces_instead_of_stepping():
    result = apply_followup(_previous(), "预算两千吧")
    assert result.request.budget_cny == 2000
    assert result.request.hours == 2


def test_a_target_word_makes_the_number_absolute():
    result = apply_followup(_previous(budget_cny=1500), "预算砍到 1000")
    assert result.request.budget_cny == 1000


def test_an_explicit_unit_wins_over_the_lowering_verb():
    """「时间砍一点吧，4 个小时够了」 is four hours, not six minus four."""
    result = apply_followup(_previous(hours=6), "时间砍一点吧，4 个小时够了")
    assert result.request.hours == 4
    # The vague half of the sentence must not be applied a second time.
    assert len(result.changes) == 1


def test_hours_are_clamped_and_a_no_op_is_not_reported_as_a_change():
    result = apply_followup(_previous(hours=12), "时间再多一点")
    assert result.recognized is False
    assert result.request.hours == 12


# --- styles: negation is positional -------------------------------------------


def test_negating_one_style_and_naming_another_swaps_them():
    result = apply_followup(_previous(styles=["tang"]), "不要唐制了，换宋制")
    assert result.request.styles == ["song"]
    assert "去掉 唐制" in result.changes[0]


def test_a_swap_cue_replaces_instead_of_appending():
    result = apply_followup(_previous(styles=["tang"]), "换成宋制")
    assert result.request.styles == ["song"]
    assert "去掉 唐制" in result.changes[0]


def test_adding_a_style_without_a_swap_cue_keeps_both():
    result = apply_followup(_previous(styles=["tang"]), "再加个宋制")
    assert result.request.styles == ["tang", "song"]


def test_a_date_word_containing_改_is_not_a_style_swap():
    result = apply_followup(_previous(styles=["tang"]), "改天拍宋制")
    assert result.request.styles == ["tang", "song"]


def test_removing_the_only_style_is_refused_rather_than_applied():
    """An empty style list is not a plan; refusing is the honest answer."""
    result = apply_followup(_previous(styles=["tang"]), "别要唐制了")
    assert result.recognized is False
    assert result.request.styles == ["tang"]
    assert "至少保留一个" in result.message


# --- commitment time ----------------------------------------------------------


def test_a_train_to_catch_becomes_a_deadline_preference():
    result = apply_followup(_previous(), "我得赶下午五点的高铁")
    assert result.recognized is True
    assert "17:00前结束" in result.request.preferences


def test_a_bare_hour_with_no_period_is_not_turned_into_a_deadline():
    """06:00 and 18:00 are both plausible; guessing one would be a lie."""
    result = apply_followup(_previous(), "我六点要坐飞机")
    assert result.recognized is False
    assert result.request.preferences == ["人少"]


def test_the_deadline_preference_replaces_an_older_one():
    first = apply_followup(_previous(), "得赶下午五点的高铁")
    second = apply_followup(first.request, "还是赶下午六点的高铁吧")
    deadlines = [
        p for p in second.request.preferences if p.endswith("前结束")
    ]
    assert deadlines == ["18:00前结束"]
