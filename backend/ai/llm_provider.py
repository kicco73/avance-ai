"""Abstract interface shared by all LLM providers.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Retry/backoff policy for transient upstream overload (HTTP 503). Lives here
# rather than in any one caller since generate_with_retry() below is shared
# by every caller that needs it (interactive chat turns, the opening-message
# generation) — a policy belonging to "how do we call a provider", not to
# any particular feature.
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0

class LLMProviderError(Exception):
    """Readable error to show on the frontend, without crashing the server."""
    message = f"AI service error."
    status_code = 503
    detail = None
    def __init__(self, message: str) -> None:
        self.detail = message

class LLMProviderUnavailableError(LLMProviderError):
    """The upstream model API is temporarily overloaded/unavailable (HTTP 503).

    Kept distinct from LLMProviderError so callers can tell a transient,
    worth-retrying failure apart from a permanent one.
    """
    message = f"AI service unavailable after {MAX_RETRIES} retries."
    status_code = 503


class LLMProviderRateLimitedError(LLMProviderError):
    """The upstream model API rejected the request for rate limiting (HTTP 429)."""
    message = "The AI service rate limit was exceeded."
    status_code = 429


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, history: list[dict]) -> str:
        """Generates the assistant's reply given the conversation history.

        `history` is a list of {"role": "user"|"assistant", "content": str}.
        Returns the reply text.
        Raises LLMProviderError with a readable message on failure
        (missing key, timeout, API error), without propagating unhandled exceptions.
        """
        raise NotImplementedError

# Awaited before each backoff sleep with (attempt, max_attempts, remaining_
# seconds) — e.g. to push a live "retrying" status frame to a websocket
# client. Optional: a caller with no one to report progress to (like a
# synchronous opening-message generation with no client watching) just omits
# it.
OnRetry = Callable[[int, int, float], Awaitable[None]]


async def generate_with_retry(
    provider: LLMProvider,
    system_prompt: str,
    history: list[dict],
    on_retry: OnRetry | None = None,
) -> str:
    """Calls provider.generate() (off the event loop, since providers make
    blocking HTTP calls), retrying on a transient overload
    (LLMProviderUnavailableError) with exponential backoff up to
    MAX_RETRIES. Any other LLMProviderError (rate-limited, or a permanent
    failure) is not retried — it propagates immediately, exactly like a
    retry-exhausted LLMProviderUnavailableError does, for the caller to
    handle however is appropriate for it."""
    attempt = 0
    while True:
        try:
            return await asyncio.to_thread(provider.generate, system_prompt, history)
        except LLMProviderUnavailableError as exc:
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
