"""The AI layer as a single service, same style as ModelService/ChatService:
constructed once in main.py from AppConfig.ai_services and passed
explicitly to whatever needs it. Exposes the old LLMProvider interface
(generate(), generate_audio_stream()) over every configured provider at
once, cascading between them as described in AiService.generate() — every
caller (ChatService, Signals) talks to this one object and never sees
LLMProvider, individual provider classes, or the cascade itself.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Iterator, NamedTuple

from config import AiServiceConfig
from ai.llm_provider import (
    LLMProvider,
    AIServiceError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
)
from ai.anthropic_provider import AnthropicProvider
from ai.gemini_provider import GeminiProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

# Retry/backoff policy for transient upstream overload (HTTP 503) on
# whichever provider is currently active — a policy belonging to "how do we
# call a provider", not to any particular feature (chat turns, signal
# computation, ...).
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0

# Awaited before each backoff sleep with (attempt, max_attempts, remaining_
# seconds) — e.g. to push a live "retrying" status frame to a websocket
# client. Optional: a caller with no one to report progress to (like
# signal computation) just omits it.
OnRetry = Callable[[int, int, float], Awaitable[None]]


class _Provider(NamedTuple):
    label: str
    provider: LLMProvider


class AiService(object):
    """One shared "current provider" pointer behind every AI call in the
    backend. A call always starts from whichever provider the pointer is
    on:

    - On success, the pointer doesn't move — later calls keep using the
      same provider as long as it keeps working.
    - A transient overload (LLMProviderUnavailableError, e.g. HTTP 503) is
      retried in place with backoff (see _generate_with_backoff) before
      giving up on that provider.
    - A provider found outright unavailable — that retry exhausted, or a
      rate-limit/quota error (LLMProviderRateLimitedError), which is never
      retried — makes the pointer advance to the next provider (wrapping
      after the last), and the same call retries immediately on it.
    - One full pass over every provider is the most a single call makes:
      if every provider fails once, the call stops and raises the last
      error seen, rather than looping a second time.
    - Any other LLMProviderError (a permanent, provider-agnostic failure,
      e.g. a bad request) propagates immediately without moving the
      pointer — every provider would fail it the same way.
    """

    def __init__(self, ai_service_config: list[AiServiceConfig]) -> None:
        if not ai_service_config:
            raise ValueError("AiService needs at least one configured provider.")
        self._providers = [
            _Provider(f"{service.name}/{service.model}", self._build_provider(service))
            for service in ai_service_config
        ]
        self._index = 0

    @staticmethod
    def _build_provider(service: AiServiceConfig) -> LLMProvider:
        if service.name not in _PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid provider name: {service.name!r}. Must be one of: "
                f"{', '.join(_PROVIDER_CLASSES.keys())}"
            )
        return _PROVIDER_CLASSES[service.name](api_key=service.key, model=service.model)

    @property
    def _current(self) -> LLMProvider:
        return self._providers[self._index].provider

    def _advance(self) -> None:
        self._index = (self._index + 1) % len(self._providers)
        logger.warning(
            "Switching AI provider to '%s' (entry #%d).",
            self._providers[self._index].label,
            self._index + 1,
        )

    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None = None,
    ) -> str:
        """Generates the assistant's reply given the conversation history,
        cascading across providers as described on the class. `history` is
        a list of {"role": "user"|"assistant", "content": str}. Raises
        LLMProviderError if every provider in one full pass has failed."""
        last_error: AIServiceError | None = None
        for _ in range(len(self._providers)):
            try:
                return await self._generate_with_backoff(self._current, system_prompt, history, on_retry)
            except (AIServiceProviderRateLimitedError, AIServiceProviderUnavailableError) as exc:
                last_error = exc
                self._advance()
        raise last_error

    @staticmethod
    async def _generate_with_backoff(
        provider: LLMProvider,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None,
    ) -> str:
        """Calls provider.generate() (off the event loop, since providers
        make blocking HTTP calls), retrying on a transient overload
        (LLMProviderUnavailableError) with exponential backoff up to
        MAX_RETRIES. Any other error — including a retry-exhausted
        LLMProviderUnavailableError — propagates to generate()'s cascade
        loop above."""
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(provider.generate, system_prompt, history)
            except AIServiceProviderUnavailableError as exc:
                logger.error(
                    "LLM provider temporarily unavailable (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    exc,
                )
                if attempt >= MAX_RETRIES:
                    raise
                attempt += 1
                remaining = BASE_DELAY_SECONDS * 2 ** (attempt - 1)
                while remaining > 0:
                    if on_retry:
                        await on_retry(attempt, MAX_RETRIES, round(remaining, 1))
                    step = min(1.0, remaining)
                    await asyncio.sleep(step)
                    remaining -= step

    def generate_audio_stream(self, text: str) -> Iterator[tuple[bytes, int]] | None:
        """Audio is best-effort and never raises (see LLMProvider), so it
        never advances the cascade itself — it just uses whichever
        provider text generation has currently settled on."""
        return self._current.generate_audio_stream(text)
