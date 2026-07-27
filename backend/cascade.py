"""Generic fallback cascade: ordered providers behind a "current" pointer,
retry-then-cascade on failure. Shared by AiService, TalkService and
ListenService, each with their own independent instance.
"""
from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import Awaitable, Callable, Generic, NamedTuple, TypeVar

logger = logging.getLogger(__name__)

# Retry/backoff policy before giving up on a transiently unavailable provider.
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0


class ProviderError(Exception):
    """Shared transient/permanent taxonomy for AiService and TalkService
    — defined once so neither duplicates the other's classification."""
    message = "Provider service error."
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    detail = None
    def __init__(self, message: str) -> None:
        self.detail = message


class ProviderUnavailableError(ProviderError):
    """Transient overload (e.g. HTTP 503) — retried with backoff."""
    message = "Service unavailable after every retry."
    status_code = HTTPStatus.SERVICE_UNAVAILABLE


class ProviderRateLimitedError(ProviderError):
    """Rate limit/quota (e.g. HTTP 429) — never retried, cascades immediately."""
    message = "The service rate limit was exceeded."
    status_code = HTTPStatus.TOO_MANY_REQUESTS

# Awaited before each backoff sleep with (attempt, max_attempts, remaining
# seconds) — e.g. to push a "retrying" status to a client. Optional.
OnRetry = Callable[[int, int, float], Awaitable[None]]

Provider = TypeVar("Provider")
Result = TypeVar("Result")


class _Entry(NamedTuple):
    label: str
    provider: object


class ProviderCascade(Generic[Provider]):
    """Shared "current provider" pointer, advanced only on failure: a
    transient error retries in place with backoff, an unavailable/
    rate-limited one advances immediately. One pass max per call; any
    other exception propagates untouched."""

    def __init__(self, providers: list[tuple[str, Provider]], *, kind: str) -> None:
        if not providers:
            raise ValueError(f"{kind} cascade needs at least one provider.")
        self._kind = kind
        self._entries = [_Entry(label, provider) for label, provider in providers]
        self._index = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def current(self) -> Provider:
        return self._entries[self._index].provider

    def advance(self) -> None:
        self._index = (self._index + 1) % len(self._entries)
        logger.warning(
            "Switching %s provider to '%s' (entry #%d).",
            self._kind,
            self._entries[self._index].label,
            self._index + 1,
        )

    async def call_with_retry(
        self,
        call: Callable[[Provider], Result],
        *,
        unavailable: type[BaseException],
        rate_limited: type[BaseException],
        on_retry: OnRetry | None = None,
    ) -> Result:
        """Calls call(self.current) off the event loop, cascading across
        providers as described on the class. Raises the last error seen
        if every provider fails within one pass."""
        last_error: BaseException | None = None
        for _ in range(len(self._entries)):
            try:
                return await self._call_with_backoff(call, unavailable, on_retry)
            except (rate_limited, unavailable) as exc:
                last_error = exc
                self.advance()
        raise last_error

    async def _call_with_backoff(
        self,
        call: Callable[[Provider], Result],
        unavailable: type[BaseException],
        on_retry: OnRetry | None,
    ) -> Result:
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(call, self.current)
            except unavailable as exc:
                logger.error(
                    "%s provider temporarily unavailable (attempt %d/%d): %s",
                    self._kind,
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
