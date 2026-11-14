"""The pipeline budget must bound the slowest stage, not just the cheap ones."""

from __future__ import annotations

import time

from shotplan.agents.base import GenerationResult
from shotplan.agents.openai_compatible import OpenAICompatibleProvider
from shotplan.config import Settings
from shotplan.domain.errors import DeadlineExceeded, ProviderError
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


class DeadlineSpy:
    name = "spy"
    prompt_version = "planner-v2"
    accepts_deadline = True

    def __init__(self):
        self.deadlines: list[float] = []

    def generate(self, request, context, repair_hint=None, deadline=None):
        self.deadlines.append(deadline)
        return GenerationResult(payload={}, prompt_version=self.prompt_version)


def test_pipeline_passes_deadline_only_to_providers_that_declare_support():
    spy = DeadlineSpy()
    run_plan(PlanRequest(), provider=spy, settings=_settings(pipeline_budget_s=30.0), max_repairs=0)
    assert len(spy.deadlines) == 1
    assert isinstance(spy.deadlines[0], float)

    class LegacyProvider(DeadlineSpy):
        accepts_deadline = False

        def generate(self, request, context, repair_hint=None):
            return super().generate(request, context, repair_hint)

    legacy = LegacyProvider()
    run_plan(PlanRequest(), provider=legacy, settings=_settings(pipeline_budget_s=30.0), max_repairs=0)
    assert legacy.deadlines == [None]


def test_generate_that_outlives_the_budget_returns_an_error_not_a_plan():
    class SlowProvider(DeadlineSpy):
        def generate(self, request, context, repair_hint=None, deadline=None):
            time.sleep(0.05)
            return super().generate(request, context, repair_hint, deadline)

    result = run_plan(
        PlanRequest(), provider=SlowProvider(),
        settings=_settings(pipeline_budget_s=0.01), max_repairs=0,
    )
    assert result.status == "error"
    assert any("预算" in issue.message for issue in result.validation.issues)


def test_provider_caps_each_http_timeout_by_the_remaining_budget():
    provider = OpenAICompatibleProvider(
        _settings(llm_timeout=120, llm_attempts=5, llm_adaptive_budget=False)
    )
    seen: list[float] = []

    def fake_post(body, timeout):
        seen.append(timeout)
        raise ProviderError("HTTP 503 from https://example.invalid")

    provider._post = fake_post  # type: ignore[method-assign]
    deadline = time.perf_counter() + 0.5
    try:
        provider.generate(PlanRequest(), {"city": "xian", "city_name": "西安"}, deadline=deadline)
    except (ProviderError, DeadlineExceeded):
        pass
    assert seen and all(t <= 0.5 for t in seen), f"timeouts escaped the deadline: {seen}"


def test_an_already_expired_deadline_raises_immediately():
    provider = OpenAICompatibleProvider(_settings(llm_attempts=5))
    called = []

    def fake_post(body, timeout):
        called.append(timeout)
        raise ProviderError("HTTP 503 from https://example.invalid")

    provider._post = fake_post  # type: ignore[method-assign]
    try:
        provider.generate(
            PlanRequest(), {"city": "xian", "city_name": "西安"}, deadline=time.perf_counter() - 1
        )
    except DeadlineExceeded:
        pass
    else:
        raise AssertionError("an expired deadline must not start a new HTTP call")
    assert called == []
