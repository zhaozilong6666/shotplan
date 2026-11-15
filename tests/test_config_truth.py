"""One Settings object per run: the configuration truth-source contract."""

from __future__ import annotations

import inspect

import pytest

from shotplan.agents import build_provider
from shotplan.agents.openai_compatible import OpenAICompatibleProvider
from shotplan.config import Settings, get_settings
from shotplan.domain.errors import ConfigError
from shotplan.domain.models import PlanRequest
from shotplan.pipeline import run_plan


def _settings(**overrides) -> Settings:
    base = {
        "provider": "openai_compatible",
        "llm_base_url": "https://example.invalid/v1",
        "llm_api_key": "test-key",
        "llm_model": "fake-model",
    }
    base.update(overrides)
    return Settings(**base)


def test_build_provider_requires_explicit_settings():
    signature = inspect.signature(build_provider)
    assert signature.parameters["settings"].default is inspect.Parameter.empty, (
        "an implicit Settings.from_env() fallback recreates the two-object bug"
    )


def test_run_plan_rejects_a_provider_built_from_another_settings_object():
    provider = OpenAICompatibleProvider(_settings(llm_timeout=11))
    with pytest.raises(ConfigError, match="different Settings"):
        run_plan(PlanRequest(), provider=provider, settings=_settings(llm_timeout=99))


def test_the_same_settings_object_is_accepted():
    cfg = _settings(llm_timeout=7)
    provider = build_provider(cfg)
    result = run_plan(
        PlanRequest(), provider=provider, settings=cfg, max_repairs=0
    )
    assert result.status in ("ok", "error", "infeasible")


def test_get_settings_is_the_single_entry_and_reads_env(monkeypatch):
    import shotplan.pipeline as pipeline

    monkeypatch.setenv("SHOTPLAN_LLM_MODEL", "single-entry-model")
    monkeypatch.setenv("SHOTPLAN_PROVIDER", "mock")
    cfg = get_settings()
    assert cfg.llm_model == "single-entry-model"

    # run_plan without an explicit cfg must go through the SAME resolver,
    # not a bare Settings.from_env() that would skip .env handling.
    calls: list[int] = []
    original = pipeline.get_settings

    def spy():
        calls.append(1)
        return original()

    monkeypatch.setattr(pipeline, "get_settings", spy)
    run_plan(PlanRequest(), max_repairs=0)
    assert calls == [1]


def test_resolved_is_complete_and_never_contains_secrets():
    cfg = _settings(llm_api_key="super-secret", api_key="deploy-secret")
    data = cfg.resolved()
    assert data["llm_api_key"] is True and data["api_key"] is True
    assert "super-secret" not in str(data) and "deploy-secret" not in str(data)
    assert data["llm_model"] == "fake-model"
    assert isinstance(data["db_path"], str)
