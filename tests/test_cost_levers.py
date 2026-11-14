"""Cost levers must change the request body, and only when asked to."""

from __future__ import annotations

import json

from shotplan.agents.openai_compatible import OpenAICompatibleProvider
from shotplan.config import Settings
from shotplan.domain.models import PlanRequest
from shotplan.knowledge.tools import get_tools


def _context() -> dict:
    """The real retrieval context, so the prompt builder sees the keys it needs."""
    return get_tools().context_for(PlanRequest())


def _settings(**overrides) -> Settings:
    base = {
        "provider": "openai_compatible",
        "llm_base_url": "https://example.invalid/v1",
        "llm_api_key": "test-key",
        "llm_model": "fake-model",
    }
    base.update(overrides)
    return Settings(**base)


def _capture(monkeypatch, provider) -> dict:
    captured: dict = {}

    def fake_post(body, timeout):
        captured.update(body)
        return {
            "choices": [{"message": {"content": json.dumps({"slots": []})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    return captured


def _run(monkeypatch, **overrides) -> dict:
    provider = OpenAICompatibleProvider(_settings(**overrides))
    body = _capture(monkeypatch, provider)
    provider.generate(PlanRequest(), _context())
    return body


def test_levers_are_absent_by_default(monkeypatch):
    body = _run(monkeypatch)
    assert "max_tokens" not in body
    assert "reasoning_effort" not in body
    assert body["temperature"] == 0.2


def test_levers_are_sent_when_configured(monkeypatch):
    body = _run(monkeypatch, llm_max_tokens=1800, llm_reasoning_effort="low", llm_temperature=0.0)
    assert body["max_tokens"] == 1800
    assert body["reasoning_effort"] == "low"
    assert body["temperature"] == 0.0


def test_unsupported_lever_is_dropped_instead_of_failing(monkeypatch):
    provider = OpenAICompatibleProvider(_settings(llm_max_tokens=1200, llm_reasoning_effort="low"))
    seen: list[dict] = []

    def fake_post(body, timeout):
        seen.append(dict(body))
        if "reasoning_effort" in body:
            from shotplan.domain.errors import ProviderError

            raise ProviderError("HTTP 400: unknown field 'reasoning_effort'")
        return {
            "choices": [{"message": {"content": json.dumps({"slots": []})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    provider.generate(PlanRequest(), _context())

    assert "reasoning_effort" in seen[0]
    assert "reasoning_effort" not in seen[1], "rejected lever must be retried without it"
    assert seen[1]["max_tokens"] == 1200, "unrelated lever must survive the retry"


def test_adaptive_budget_escalates_only_when_truncated(monkeypatch):
    provider = OpenAICompatibleProvider(_settings(llm_adaptive_budget=True))
    seen: list[dict] = []

    def fake_post(body, timeout):
        seen.append(dict(body))
        reason = "length" if len(seen) == 1 else "stop"
        return {
            "choices": [{"message": {"content": json.dumps({"slots": []})}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    result = provider.generate(PlanRequest(), _context())

    assert len(seen) == 2, "a truncated answer must be retried exactly once more"
    assert seen[1]["max_tokens"] == seen[0]["max_tokens"] * 2
    assert result.metadata["truncated_retries"] == 1


def test_adaptive_budget_stops_at_the_first_cap_when_the_answer_fits(monkeypatch):
    provider = OpenAICompatibleProvider(_settings(llm_adaptive_budget=True))
    seen: list[dict] = []

    def fake_post(body, timeout):
        seen.append(dict(body))
        return {
            "choices": [{"message": {"content": json.dumps({"slots": []})}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    result = provider.generate(PlanRequest(), _context())

    assert len(seen) == 1, "no reason to pay twice when the first answer was complete"
    assert seen[0]["max_tokens"] > 0
    assert result.metadata["truncated_retries"] == 0


def test_adaptive_budget_scales_with_the_request():
    provider = OpenAICompatibleProvider(_settings(llm_adaptive_budget=True))
    short = provider._budget_ladder(PlanRequest(hours=3))
    long = provider._budget_ladder(PlanRequest(hours=9))
    assert short[0] < long[0], "a longer shoot needs more room for slots"
    assert short[1] == short[0] * 2
    assert short[-1] is None, "the last rung must be uncapped, or a plan can never finish"
