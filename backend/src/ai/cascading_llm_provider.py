"""Composite LLMProvider: presents an ordered list of LLMProviders as a
single provider with the same contract, with retry, backoff, and
ordered fallback across the list handled underneath (see cascade.py's
ProviderCascade)."""
from __future__ import annotations

from typing import AsyncIterator

from cascade import OnRetry, ProviderCascade
from ai.llm_provider import (
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    LLMProvider,
    MetadataCallback,
)


class CascadingLLMProvider(LLMProvider):
    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI")

    @property
    def current_index(self) -> int:
        return self._cascade.current_index

    # Which leaf the cascade would call right now; a pure read that
    # never advances or otherwise mutates the cascade's position.
    @property
    def current_provider(self) -> LLMProvider:
        return self._cascade.current

    # No generate() override: the LLMProvider base default calls
    # self.generate_stream, which resolves polymorphically to the
    # override below, so it's already cascade/retry-aware.
    async def generate_stream(
        self, system_prompt: str, history: list[dict]
    ) -> AsyncIterator[str]:

        def call(provider: LLMProvider) -> AsyncIterator[str]:
            return provider.generate_stream(system_prompt, history)

        stream = await self._cascade.call_with_retry(
            call,
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
        )
        async for chunk in stream:
            yield chunk

    async def generate_stream_with_schema(self, system_prompt: str , history: list[dict], schema: dict[str,str]):
        def call(provider: LLMProvider) -> AsyncIterator[str]:
            return provider.generate_stream_with_schema(system_prompt, history, schema=schema) # type: ignore

        stream = await self._cascade.call_with_retry(
            call,
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
        )
        async for chunk in stream:
            yield chunk
