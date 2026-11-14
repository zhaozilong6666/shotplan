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


def test_router_only_selects_v3_for_light_sensitive_briefs():
    from shotplan.agents.openai_compatible import _wants_light_aware_prompt
    from shotplan.domain.models import PlanRequest

    assert _wants_light_aware_prompt(
        PlanRequest(hours=3, preferences=["上午拍完", "不拍傍晚"])
    )
    assert _wants_light_aware_prompt(PlanRequest(hours=6, must_include=["shuyuanmen"]))
    assert not _wants_light_aware_prompt(PlanRequest(hours=6, preferences=["人少"]))
    assert not _wants_light_aware_prompt(PlanRequest(hours=12, preferences=["上午拍完"]))


def test_router_changes_the_system_prompt_per_request(monkeypatch):
    from shotplan.agents.openai_compatible import OpenAICompatibleProvider
    from shotplan.agents.prompts import get_system_prompt
    from shotplan.domain.models import PlanRequest
    from shotplan.knowledge.tools import get_tools

    provider = OpenAICompatibleProvider(_settings(prompt_router=True))
    seen: list[str] = []

    def fake_post(body, timeout):
        seen.append(body["messages"][0]["content"])
        return {
            "choices": [{"message": {"content": '{"slots": []}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    tools = get_tools()
    normal = PlanRequest(hours=6, preferences=["人少"])
    light = PlanRequest(hours=3, preferences=["上午拍完", "不拍傍晚"])

    a = provider.generate(normal, tools.context_for(normal))
    b = provider.generate(light, tools.context_for(light))

    assert seen[0] == get_system_prompt("planner-v2")
    assert seen[1] == get_system_prompt("planner-v3")
    assert a.metadata["prompt_version"] == "planner-v2"
    assert b.metadata["prompt_version"] == "planner-v3"


def test_router_is_off_by_default(monkeypatch):
    from shotplan.agents.openai_compatible import OpenAICompatibleProvider
    from shotplan.domain.models import PlanRequest
    from shotplan.knowledge.tools import get_tools

    provider = OpenAICompatibleProvider(_settings())
    seen: list[str] = []

    def fake_post(body, timeout):
        seen.append(body["messages"][0]["content"])
        return {
            "choices": [{"message": {"content": '{"slots": []}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    request = PlanRequest(hours=3, preferences=["上午拍完", "不拍傍晚"])
    result = provider.generate(request, get_tools().context_for(request))
    assert result.metadata["prompt_version"] == "planner-v2"
    assert len(seen) == 1
