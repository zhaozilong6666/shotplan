"""Optional real-model provider using only the standard library.

urllib instead of the openai SDK is a deliberate choice: it keeps the
dependency graph at zero, makes retries and timeouts explicit, and shows the
actual HTTP contract rather than hiding it behind a client library.
Any OpenAI-compatible endpoint works (OpenAI, DeepSeek, local Ollama, vLLM).
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any

from ..config import Settings
from ..domain.errors import ProviderError
from ..domain.models import PlanRequest
from .base import GenerationResult
from .prompts import PROMPT_VERSION, build_user_prompt, get_system_prompt

# Approximate prices in CNY per 1K tokens. Used only for trace accounting;
# never for billing claims.
PRICE_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.0011, 0.0043),
    "gpt-4o": (0.0180, 0.0720),
    "deepseek-chat": (0.0020, 0.0080),
    # Relay list price seen 2026-11: $0.132 / 1M input on weekends, output
    # assumed at the usual 4x ratio. Still an estimate, not a bill.
    "deepseek-v4.1-flash": (0.0010, 0.0040),
    "deepseek-v4.1-flash-261017": (0.0010, 0.0040),
}
DEFAULT_PRICE = (0.0070, 0.0210)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# Exposed so docs/pipeline-contract.md can compute the worst-case latency
# from the same numbers the code uses instead of restating them by hand.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5


def _finish_reason(data: dict[str, Any]) -> str:
    """Why the model stopped. `length` means our budget cut it off."""
    try:
        return str(data["choices"][0].get("finish_reason") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


# Morning- or night-only locations from the knowledge base: a brief that pins
# one of these is a candidate for the light-aware prompt.
_LIGHT_SENSITIVE_LOCATIONS = {
    "shuyuanmen",
    "sun_yat_sen_mausoleum",
    "humble_administrator_garden",
    "jinji_lake",
    "leifeng_pagoda",
}
_LIGHT_SENSITIVE_WORDS = (
    "上午", "中午", "正午", "晨光", "黄金", "傍晚", "夜景",
    "室内为主", "下午不出外景",
)


def _wants_light_aware_prompt(request: PlanRequest) -> bool:
    if request.hours > 10:
        return False  # a full day has room for both windows; no pressure
    preferences = " ".join(request.preferences or [])
    if any(word in preferences for word in _LIGHT_SENSITIVE_WORDS):
        return True
    return bool(set(request.must_include or []) & _LIGHT_SENSITIVE_LOCATIONS)


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICE_PER_1K.get(model, DEFAULT_PRICE)
    return (tokens_in / 1000) * price_in + (tokens_out / 1000) * price_out


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, settings: Settings, prompt_version: str | None = None) -> None:
        if not settings.llm_api_key:
            raise ProviderError(
                "SHOTPLAN_LLM_API_KEY is empty. Set it in .env, or use "
                "SHOTPLAN_PROVIDER=mock to run fully offline."
            )
        self.settings = settings
        self.prompt_version = prompt_version or PROMPT_VERSION
        self._system_prompt = get_system_prompt(self.prompt_version)

    def _post(self, body: dict[str, Any], timeout: int) -> dict[str, Any]:
        url = f"{self.settings.llm_base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "User-Agent": "shotplan/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"network failure calling {url}: {exc}") from exc

    def _budget_ladder(self, request: PlanRequest) -> list[int | None]:
        """Output budgets to try, cheapest first.

        A plan is roughly `520 + 300 × slots` tokens of JSON. The model happily
        writes 4-5k because nothing stops it, and output is ~90% of the bill.
        So: estimate, cap, and only pay more when the answer was actually cut
        off (`finish_reason == "length"`). Worst case is one extra call; usual
        case saves several thousand output tokens per case.
        """
        explicit = self.settings.llm_max_tokens or None
        if not self.settings.llm_adaptive_budget:
            return [explicit]
        slots = max(4, min(8, math.ceil(request.hours / 1.5) + 2))
        estimate = 520 + 300 * slots
        first = min(explicit, estimate) if explicit else estimate
        return [first, first * 2, None]

    def _complete(self, body: dict[str, Any]) -> dict[str, Any]:
        """One request with the existing retry / capability-fallback policy."""
        last_error: ProviderError | None = None
        attempts = max(1, self.settings.llm_attempts)
        for attempt in range(attempts):
            try:
                return self._post(body, self.settings.llm_timeout)
            except ProviderError as exc:
                last_error = exc
                message = str(exc)
                # Some compatible endpoints reject response_format. Drop it once.
                if "response_format" in message and "response_format" in body:
                    body.pop("response_format", None)
                    continue
                # Same for the cost levers: an endpoint that does not know them
                # should cost us one retry, not the whole request.
                if "reasoning_effort" in message and "reasoning_effort" in body:
                    body.pop("reasoning_effort", None)
                    continue
                if "max_tokens" in message and "max_tokens" in body:
                    body.pop("max_tokens", None)
                    continue
                if not any(f"HTTP {code}" in message for code in _RETRYABLE_STATUS):
                    if "network failure" not in message:
                        raise
                if attempt == attempts - 1:
                    raise
                time.sleep(BACKOFF_BASE_S * (2**attempt))
        raise last_error or ProviderError("empty provider response")

    def generate(
        self,
        request: PlanRequest,
        context: dict[str, Any],
        repair_hint: str | None = None,
    ) -> GenerationResult:
        prompt_used = self.prompt_version
        if self.settings.prompt_router and _wants_light_aware_prompt(request):
            prompt_used = "planner-v3"
        messages: list[dict[str, str]] = [
            {"role": "system", "content": get_system_prompt(prompt_used)},
            {"role": "user", "content": build_user_prompt(request, context, repair_hint)},
        ]
        base: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "response_format": {"type": "json_object"},
        }
        if self.settings.llm_reasoning_effort:
            base["reasoning_effort"] = self.settings.llm_reasoning_effort

        started = time.perf_counter()
        data: dict[str, Any] | None = None
        truncated = 0
        budget_used: int | None = None
        for budget in self._budget_ladder(request):
            body = dict(base)
            if budget:
                body["max_tokens"] = budget
            data = self._complete(body)
            budget_used = budget
            if _finish_reason(data) != "length":
                break
            truncated += 1
        if data is None:
            raise ProviderError("empty provider response")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {str(data)[:300]}") from exc

        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        elapsed = (time.perf_counter() - started) * 1000

        return GenerationResult(
            payload=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_cny=_estimate_cost(self.settings.llm_model, tokens_in, tokens_out),
            duration_ms=elapsed,
            model=self.settings.llm_model,
            metadata={
                "prompt_version": prompt_used,
                "repair": bool(repair_hint),
                "output_budget": budget_used,
                "truncated_retries": truncated,
            },
        )
