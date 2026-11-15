"""Boundary hardening: error text, top-level fallback, repair visibility, locks."""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from shotplan.agents.base import GenerationResult
from shotplan.agents.mock import MockProvider
from shotplan.agents.openai_compatible import OpenAICompatibleProvider
from shotplan.config import Settings
from shotplan.domain.errors import ProviderError
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan
from shotplan.storage import RunStore


def _settings(**overrides) -> Settings:
    base = {
        "provider": "openai_compatible",
        "llm_base_url": "https://user:token@example.invalid/v1/chat",
        "llm_api_key": "sk-real-secret-value",
        "llm_model": "fake-model",
    }
    base.update(overrides)
    return Settings(**base)


def test_provider_error_strips_credentials_and_url_path(monkeypatch):
    provider = OpenAICompatibleProvider(_settings())

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            502,
            "bad gateway",
            {},
            io.BytesIO(b'{"error":"sk-abcdefghijklmnop failed"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as caught:
        provider._post({}, 1)

    message = str(caught.value)
    assert "sk-abcdefghijklmnop" not in message
    assert "sk-real-secret-value" not in message
    assert "/v1/chat" not in message
    assert "user:token@" not in message
    assert "example.invalid" in message
    assert "HTTP 502" in message


def test_unexpected_pipeline_exception_becomes_error_result_not_500(monkeypatch):
    import shotplan.pipeline as pipeline

    def broken(*args, **kwargs):
        raise RuntimeError("knowledge lookup exploded")

    monkeypatch.setattr(pipeline, "check_feasibility", broken)
    result = run_plan(PlanRequest(), max_repairs=0)
    assert result.status == "error"
    assert result.validation.hard_pass is False
    assert any("PIPELINE_ERROR" == issue.code for issue in result.validation.issues)
    assert "RuntimeError" in result.validation.issues[0].message


def test_failed_repair_is_visible_instead_of_a_silent_ok():
    class RepairFails:
        name = "fake"
        prompt_version = "planner-v2"

        def generate(self, request, context, repair_hint=None) -> GenerationResult:
            if repair_hint:
                raise ProviderError("repair provider down")
            return MockProvider("naive").generate(request, context)

    result = run_plan(
        PlanRequest(),
        provider=RepairFails(),
        settings=Settings(provider="mock", max_repairs=1),
        max_repairs=1,
    )
    codes = {issue.code for issue in result.validation.issues}
    assert result.status == "ok", "a failed repair is a degradation, not an outage"
    assert "REPAIR_FAILED" in codes, "a swallowed repair must be marked"
    assert any(issue.severity == "soft" and issue.code == "REPAIR_FAILED"
               for issue in result.validation.issues)


def test_write_locks_are_scoped_to_each_database_file(tmp_path):
    first = RunStore(tmp_path / "a.db")
    second = RunStore(tmp_path / "b.db")
    again = RunStore(tmp_path / "a.db")
    assert first._write_lock() is not second._write_lock()
    assert first._write_lock() is again._write_lock()
