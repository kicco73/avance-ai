"""The AI layer as a single service, same style as ModelService/ChatService:
constructed once in main.py from AppConfig.ai_services and passed
explicitly to whatever needs it. Exposes the old LLMProvider interface
(generate()) over every configured provider at once, cascading between
them via cascade.ProviderCascade (see AiService.generate()) — every
caller (ChatService, Signals) talks to this one object and never sees
LLMProvider, individual provider classes, or the cascade itself.
"""
from __future__ import annotations

from config import AiServiceConfig
from cascade import OnRetry, ProviderCascade
from ai.llm_provider import (
    LLMProvider,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
)
from ai.anthropic_provider import AnthropicProvider
from ai.gemini_provider import GeminiProvider

_PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


class AiService(object):
    def __init__(self, ai_service_config: list[AiServiceConfig]) -> None:
        providers = [
            (f"{service.name}/{service.model}", self._build_provider(service))
            for service in ai_service_config
        ]
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI")

    @staticmethod
    def _build_provider(service: AiServiceConfig) -> LLMProvider:
        if service.name not in _PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid provider name: {service.name!r}. Must be one of: "
                f"{', '.join(_PROVIDER_CLASSES.keys())}"
            )
        return _PROVIDER_CLASSES[service.name](api_key=service.key, model=service.model)

    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None = None,
    ) -> str:
        """Generates the assistant's reply given the conversation history,
        cascading across providers (see cascade.ProviderCascade). `history`
        is a list of {"role": "user"|"assistant", "content": str}. Raises
        AIServiceError if every provider in one full pass has failed."""
        return await self._cascade.call_with_retry(
            lambda provider: provider.generate(system_prompt, history),
            unavailable=AIServiceProviderUnavailableError,
            rate_limited=AIServiceProviderRateLimitedError,
            on_retry=on_retry,
        )
