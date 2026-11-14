"""Prompt versions must be selectable at runtime, or A/B is impossible."""

from __future__ import annotations

import pytest

from shotplan.agents import build_provider
from shotplan.agents.prompts import PROMPT_CHANGELOG, SYSTEM_PROMPTS, get_system_prompt
from shotplan.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "provider": "openai_compatible",
        "llm_base_url": "https://example.invalid/v1",
        "llm_api_key": "test-key",
        "llm_model": "fake-model",
    }
    base.update(overrides)
    return Settings(**base)


def test_every_prompt_has_a_changelog_entry():
    versions = {entry["version"] for entry in PROMPT_CHANGELOG}
    assert versions == set(SYSTEM_PROMPTS)


def test_v3_names_the_enum_and_the_light_windows():
    prompt = get_system_prompt("planner-v3")
    for activity in ("makeup", "travel", "shoot", "rest", "backup"):
        assert activity in prompt
    assert "best_light" in prompt
    assert "60 minutes" in prompt


def test_settings_select_the_prompt_version():
    provider = build_provider(_settings(prompt_version="planner-v3"))
    assert provider.prompt_version == "planner-v3"
    default = build_provider(_settings())
    assert default.prompt_version == "planner-v2"


def test_unknown_prompt_version_fails_loudly():
    with pytest.raises(KeyError):
        build_provider(_settings(prompt_version="planner-v9"))
