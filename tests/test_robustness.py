"""Robustness contracts: garbage in must still produce a structured answer out.

The request model is fed straight from JSON, so every field can be any type at
runtime. These tests pin the three layers that keep that from becoming a 500:
total coercion in ``PlanRequest.normalized()``, a structured refusal for
unparseable dates, and ``status="error"`` when a provider or parser fails.
"""

from __future__ import annotations

import math

import pytest

from shotplan.agents.base import GenerationResult
from shotplan.domain.errors import ProviderError
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan

JUNK = [
    None,
    "",
    "abc",
    "6h",
    float("nan"),
    float("inf"),
    -1,
    10**12,
    True,
    [],
    {},
    [[1]],
    "🎎" * 40,
]


@pytest.mark.parametrize("junk", JUNK)
def test_normalize_never_raises_and_clamps(junk):
    req = PlanRequest.from_dict(
        {"city": junk, "date": junk, "hours": junk, "budget_cny": junk,
         "party_size": junk, "styles": junk, "preferences": junk}
    )
    assert isinstance(req.city, str)
    assert 1.0 <= req.hours <= 12.0
    assert req.budget_cny >= 0
    assert req.party_size >= 1
    assert isinstance(req.styles, list)


def test_nan_and_inf_hours_fall_back_to_default():
    # Non-finite numbers are junk, not "very large": they must not survive the
    # clamp, so both NaN and ±inf degrade to the documented default.
    assert PlanRequest(hours=float("nan")).normalized().hours == 6.0
    assert PlanRequest(hours=float("inf")).normalized().hours == 6.0
    assert PlanRequest(hours=float("-inf")).normalized().hours == 6.0
    assert PlanRequest(hours=99).normalized().hours == 12.0
    assert PlanRequest(hours=-99).normalized().hours == 1.0


def test_numeric_strings_are_accepted():
    req = PlanRequest.from_dict({"hours": "5", "budget_cny": "1800", "party_size": "2"})
    assert req.hours == 5.0
    assert req.budget_cny == 1800
    assert req.party_size == 2


def test_a_bare_string_style_is_one_style_not_five_characters():
    assert PlanRequest.from_dict({"styles": "tang"}).styles == ["tang"]
    assert PlanRequest.from_dict({"styles": None}).styles == []


def test_invalid_date_is_a_structured_refusal():
    result = run_plan(PlanRequest.from_dict({"date": "2026-02-30"}))
    assert result.status == "infeasible"
    codes = {reason["code"] for reason in result.feasibility["reasons"]}
    assert "DATE_INVALID" in codes
    assert result.validation.hard_pass is False


class _FailingProvider:
    name = "failing"
    prompt_version = "failing-v1"

    def generate(self, request, context, repair_hint=None):
        raise ProviderError("upstream 503")


class _GarbageProvider(_FailingProvider):
    name = "garbage"

    def generate(self, request, context, repair_hint=None):
        return GenerationResult(payload={"slots": []}, model="garbage")


def test_provider_failure_returns_error_status_not_an_exception():
    result = run_plan(PlanRequest(), provider=_FailingProvider())
    assert result.status == "error"
    assert result.validation.hard_pass is False
    assert [i.code for i in result.validation.issues] == ["PIPELINE_ERROR"]
    assert result.trace.success is False


def test_unparseable_output_returns_error_status():
    result = run_plan(PlanRequest(), provider=_GarbageProvider())
    assert result.status == "error"
    assert "slots" in result.validation.issues[0].message


def test_error_status_is_json_serialisable():
    import json

    payload = run_plan(PlanRequest(), provider=_FailingProvider()).to_dict()
    assert json.loads(json.dumps(payload))["status"] == "error"


def test_absurd_party_size_does_not_break_the_plan():
    result = run_plan(PlanRequest.from_dict({"party_size": 10**9, "budget_cny": 5000}))
    assert result.status in {"ok", "infeasible"}
    assert math.isfinite(result.validation.score)
