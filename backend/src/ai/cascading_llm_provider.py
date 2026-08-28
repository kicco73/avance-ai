"""LLMProvider fallback wrappers: AutoLiveLLMProvider presents an ordered
list of LLMProviders as a single transparent provider — one attempt, no
retry, advances the pointer on failure so the NEXT call reaches the next
provider. AutoTestLLMProvider reinforces it with in-place backoff retry and
an exhaustive cascade across every provider before giving up (see
cascade.py's ProviderCascade for the shared pointer bookkeeping)."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from cascade import BASE_DELAY_SECONDS, MAX_RETRIES, ProviderCascade
from ai.llm_provider import (
    AIServiceProviderPermanentError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    LLMProvider,
    MetadataCallback,
)
from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

_FAILOVER_ERRORS = (
    AIServiceProviderUnavailableError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderPermanentError,
)


class AutoLiveLLMProvider(LLMProvider):
    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        self._cascade: ProviderCascade[LLMProvider] = ProviderCascade(providers, kind="AI (live)")

    @property
    def current_index(self) -> int:
        return self._cascade.current_index

    # Which leaf the cascade would call right now; a pure read that
    # never advances or otherwise mutates the cascade's position.
    @property
    def current_provider(self) -> LLMProvider:
        return self._cascade.current

    def get_total_tokens(self) -> int:
        """Sums every wrapped provider's own counter, not just the
        currently-active one — a fallback that already burned tokens on an
        earlier provider before advancing must still count."""
        return sum(provider.get_total_tokens() for provider in self._cascade.providers)

    def get_input_tokens(self, prompt: str) -> int:
        return self.current_provider.get_input_tokens(prompt)

    async def generate_stream_with_schema(
        self, system_prompt: str, history: list[dict], schema: dict[str, str]
    ) -> AsyncIterator[str]:
        provider = self._cascade.current
        try:
            async for chunk in provider.generate_stream_with_schema(system_prompt, history, schema=schema):  # type: ignore
                yield chunk
        except _FAILOVER_ERRORS as exc:
            logger.error(f"AI (live) provider #{self._cascade.current_index + 1} failed: {type(exc).__name__}: {exc}")
            self._cascade.advance()
            raise


class AutoTestLLMProvider(AutoLiveLLMProvider):
    """Exhaustive cascade: retries the current provider with backoff on a
    transient failure, advancing to the next provider once every retry on
    the current one is exhausted — but only as long as nothing has been
    yielded to the caller yet. Once any chunk has gone out, a second
    provider's response can never be safely appended after it (two
    independent generations concatenated into one stream/JSON document is
    just corruption, not a retry), so any further failure is raised
    immediately instead of advancing — the caller sees a clean error
    rather than a spliced, malformed response. Reports each retry/failover
    through `on_metadata` as a warning, when given. Once every provider has
    been tried once and none succeeded, there is no "next" left within this
    call — the failure is raised as AIServiceProviderPermanentError rather
    than TryAgainError, so a caller that reschedules on TryAgainError alone
    doesn't loop forever re-hitting the same exhausted cascade."""

    async def generate_stream_with_schema(
        self,
        system_prompt: str,
        history: list[dict],
        schema: dict[str, str],
        on_metadata: MetadataCallback | None = None,
    ) -> AsyncIterator[str]:
        last_error: BaseException | None = None
        for _ in range(len(self._cascade)):
            provider = self._cascade.current
            index = self._cascade.current_index
            attempt = 0
            yielded = False
            while True:
                try:
                    async for chunk in provider.generate_stream_with_schema(system_prompt, history, schema=schema):  # type: ignore
                        yielded = True
                        yield chunk
                    return
                except AIServiceProviderUnavailableError as exc:
                    last_error = exc
                    if yielded:
                        raise
                    if attempt >= MAX_RETRIES:
                        break
                    attempt += 1
                    if on_metadata is not None:
                        on_metadata("warning", f"Provider #{index + 1} unavailable, retry {attempt}/{MAX_RETRIES}: {exc}")
                    await asyncio.sleep(BASE_DELAY_SECONDS * 2 ** (attempt - 1))
                except (AIServiceProviderRateLimitedError, AIServiceProviderPermanentError) as exc:
                    last_error = exc
                    if yielded:
                        raise
                    break
            logger.error(f"AI (test) provider #{index + 1} failed: {type(last_error).__name__}: {last_error}")
            if on_metadata is not None:
                on_metadata("warning", f"Switching away from provider #{index + 1}: {last_error}")
            self._cascade.advance()
        assert last_error is not None
        raise AIServiceProviderPermanentError(
            f"Every provider in the cascade failed; last error: {last_error}"
        ) from last_error
