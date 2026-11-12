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


def build_provider(settings: Settings | None = None, *, mode: str = "good") -> PlanProvider:
    """Factory used by CLI, API and eval harness alike."""
    cfg = settings or Settings.from_env()
    if cfg.provider == "openai_compatible":
        return OpenAICompatibleProvider(cfg)
    return MockProvider(mode=mode)
