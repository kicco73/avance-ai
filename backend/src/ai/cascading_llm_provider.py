"""Composite LLMProvider: presents an ordered list of LLMProvider
implementations as a single one. From a caller's point of view this is
indistinguishable from talking to a single concrete provider — same
contract, same exceptions — with retry-in-place, backoff, ordered
fallback and logging (see cascade.py's ProviderCascade) happening
underneath. Anywhere an LLMProvider is expected, this is a legal,
swappable value — see ai_service.py's AiService.
"""
from __future__ import annotations

from typing import AsyncIterator

from cascade import OnRetry, ProviderCascade
from ai.llm_provider import (
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    LLMProvider,
)


class CascadingLLMProvider(LLMProvider):
    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI")

    @property
    def current_index(self) -> int:
        return self._cascade.current_index

    async def generate(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None
    ) -> str:
        return await self._cascade.call_with_retry(
            lambda provider: provider.generate(system_prompt, history),
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )

    async def generate_stream(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None
    ) -> AsyncIterator[str]:
        stream = await self._cascade.call_with_retry(
            lambda provider: provider.generate_stream(system_prompt, history),
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )
        async for chunk in stream:
            yield chunk
