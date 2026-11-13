"""Optional real-model provider using only the standard library.

urllib instead of the openai SDK is a deliberate choice: it keeps the
dependency graph at zero, makes retries and timeouts explicit, and shows the
actual HTTP contract rather than hiding it behind a client library.
Any OpenAI-compatible endpoint works (OpenAI, DeepSeek, local Ollama, vLLM).
"""

from __future__ import annotations

import json
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
}
DEFAULT_PRICE = (0.0070, 0.0210)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# Exposed so docs/pipeline-contract.md can compute the worst-case latency
# from the same numbers the code uses instead of restating them by hand.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5


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

    def generate(
        self,
        request: PlanRequest,
        context: dict[str, Any],
        repair_hint: str | None = None,
    ) -> GenerationResult:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": build_user_prompt(request, context, repair_hint)},
        ]
        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        last_error: ProviderError | None = None
        data: dict[str, Any] | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                data = self._post(body, self.settings.llm_timeout)
                break
            except ProviderError as exc:
                last_error = exc
                message = str(exc)
                # Some compatible endpoints reject response_format. Drop it once.
                if "response_format" in message and "response_format" in body:
                    body.pop("response_format", None)
                    continue
                if not any(f"HTTP {code}" in message for code in _RETRYABLE_STATUS):
                    if "network failure" not in message:
                        raise
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                time.sleep(BACKOFF_BASE_S * (2**attempt))

        if data is None:
            raise last_error or ProviderError("empty provider response")

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
            metadata={"prompt_version": self.prompt_version, "repair": bool(repair_hint)},
        )
