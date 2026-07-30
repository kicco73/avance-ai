from __future__ import annotations

from typing import AsyncIterator
from cascade import OnRetry, ProviderCascade
from ai.llm_provider import (
    LLMProvider,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    AIServiceConfig,
)
from ai.anthropic_provider import AnthropicProvider
from ai.gemini_provider import GeminiProvider
from ai.openai_provider import OpenAIProvider

_PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


class AiService(object):
    def __init__(self, ai_service_config: list[AIServiceConfig]) -> None:
        providers = [
            (f"{service.name}/{service.model}", self._build_provider(service))
            for service in ai_service_config
        ]
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI")

    @staticmethod
    def _build_provider(service: AIServiceConfig) -> LLMProvider:
        if service.name not in _PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid provider name: {service.name!r}. Must be one of: "
                f"{', '.join(_PROVIDER_CLASSES.keys())}"
            )
        return _PROVIDER_CLASSES[service.name](service)

    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None = None,
    ) -> str:
        """Reply text for `history` (list of {role, content}), cascading
        across providers. Raises AIServiceError if all of them fail."""
        return await self._cascade.call_with_retry(
            lambda provider: provider.generate(system_prompt, history),
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )

    async def generate_stream(
        self,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None = None,
    ) -> AsyncIterator[str]:
        """Yields reply chunks incrementally for `history` (list of {role, content}),
        cascading across providers. Raises AIServiceError if all of them fail."""
        # Ottiene l'iteratore asincrono dal provider gestito in cascata
        stream = await self._cascade.call_with_retry(
            lambda provider: provider.generate_stream(system_prompt, history),
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )

        async for chunk in stream:
            yield chunk