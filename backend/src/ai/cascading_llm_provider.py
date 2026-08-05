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
    MetadataCallback,
)


class CascadingLLMProvider(LLMProvider):
    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI")

    @property
    def current_index(self) -> int:
        return self._cascade.current_index

    # Whichever concrete leaf the cascade would call *right now* — reading
    # this is the only way anything above this wrapper (see AiService.
    # supports_metadata) can tell what it's actually talking to, since
    # generate_stream below otherwise hides that entirely, cascading
    # between leaves on failure (see cascade.py) with no visible seam.
    # Purely a read: never advances or otherwise mutates the cascade's
    # own position.
    @property
    def current_provider(self) -> LLMProvider:
        return self._cascade.current

    # generate() has no override here — LLMProvider's own shared default
    # (see its own docstring) calls self.generate_stream, which correctly
    # resolves to this class's own override below via normal polymorphic
    # dispatch, so it's already cascade/retry-aware without needing a
    # second, separate implementation of that here.
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
            return provider.generate_stream_with_schema(system_prompt, history, schema=schema)

        stream = await self._cascade.call_with_retry(
            call,
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
        )
        async for chunk in stream:
            yield chunk
