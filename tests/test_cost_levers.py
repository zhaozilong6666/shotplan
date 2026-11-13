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
