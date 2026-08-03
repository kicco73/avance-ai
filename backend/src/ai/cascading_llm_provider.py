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
    supports_on_metadata,
)


class CascadingLLMProvider(LLMProvider):
    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI")

    @property
    def current_index(self) -> int:
        return self._cascade.current_index

    # Whichever concrete leaf the cascade would call *right now* — reading
    # this is the only way anything above this wrapper (see AiService.
    # supports_metadata_generate/supports_metadata_stream) can tell what
    # it's actually talking to, since generate()/generate_stream() below
    # otherwise hide that entirely, cascading between leaves on failure
    # (see cascade.py) with no visible seam. Purely a read: never advances
    # or otherwise mutates the cascade's own position.
    @property
    def current_provider(self) -> LLMProvider:
        return self._cascade.current

    async def generate(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None, on_metadata: MetadataCallback | None = None
    ) -> str:
        def call(provider: LLMProvider) -> str:
            # Checked per attempt, not once up front — a failed leaf can
            # cascade to a *different* one mid-call (see cascade.py's own
            # advance()), which might not share the same on_metadata
            # support the caller originally chose this mode for. Passing
            # it to a "v1" leaf that doesn't accept it as a keyword would
            # otherwise raise a plain TypeError instead of the clean
            # AIServiceError this whole cascade exists to guarantee.
            if on_metadata is not None and supports_on_metadata(provider.generate):
                return provider.generate(system_prompt, history, on_metadata=on_metadata)
            return provider.generate(system_prompt, history)

        return await self._cascade.call_with_retry(
            call,
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )

    async def generate_stream(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None, on_metadata: MetadataCallback | None = None
    ) -> AsyncIterator[str]:
        def call(provider: LLMProvider) -> AsyncIterator[str]:
            if on_metadata is not None and supports_on_metadata(provider.generate_stream):
                return provider.generate_stream(system_prompt, history, on_metadata=on_metadata)
            return provider.generate_stream(system_prompt, history)

        stream = await self._cascade.call_with_retry(
            call,
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )
        async for chunk in stream:
            yield chunk
