"""Agent layer: providers, prompts, parsing and deterministic validation."""

from ..config import Settings
from .base import GenerationResult, PlanProvider
from .mock import MockProvider
from .openai_compatible import OpenAICompatibleProvider
from .planner import parse_plan
from .validator import validate

__all__ = [
    "GenerationResult",
    "MockProvider",
    "OpenAICompatibleProvider",
    "PlanProvider",
    "parse_plan",
    "validate",
    "build_provider",
]


def build_provider(settings: Settings, *, mode: str = "good") -> PlanProvider:
    """Factory used by CLI, API and eval harness alike.

    `settings` is required on purpose: an implicit `Settings.from_env()` here
    used to create a SECOND config object inside one API request, so the
    provider's timeout/attempts/temperature could differ from the pipeline's
    own cfg while the trace recorded only the latter. Callers must pass the
    very same object they hand to `run_plan`.
    """
    if settings.provider == "openai_compatible":
        return OpenAICompatibleProvider(settings, prompt_version=settings.prompt_version or None)
    return MockProvider(mode=mode)
