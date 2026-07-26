"""Generic fallback-cascade mechanism: an ordered list of providers behind
a single "current" pointer, advanced only on failure, with a
retry-then-cascade policy for calling whichever provider it currently
points to. Shared by ai/ai_service.py's AiService and
audio/audio_service.py's AudioService — each constructs its OWN
independent ProviderCascade instance from its own provider list; nothing
here is itself shared state between them.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Generic, NamedTuple, TypeVar

logger = logging.getLogger(__name__)

# Retry/backoff policy for a provider found transiently unavailable (e.g.
# HTTP 503), before the cascade gives up on it and moves to the next one.
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0

# Awaited before each backoff sleep with (attempt, max_attempts, remaining_
# seconds) — e.g. to push a live "retrying" status frame to a websocket
# client. Optional: a caller with no one to report progress to just omits
# it.
OnRetry = Callable[[int, int, float], Awaitable[None]]

Provider = TypeVar("Provider")
Result = TypeVar("Result")


class _Entry(NamedTuple):
    label: str
    provider: object


class ProviderCascade(Generic[Provider]):
    """One shared "current provider" pointer over `providers`, advanced
    only by call_with_retry() below on failure — never anywhere else — so
    every caller sharing the same instance always starts from wherever
    the pointer currently is:

    - On success, the pointer doesn't move.
    - A transient failure (the `unavailable` exception type passed to
      call_with_retry, e.g. HTTP 503) is retried in place with backoff up
      to MAX_RETRIES before giving up on that provider.
    - A provider found outright unavailable — that retry exhausted, or a
      `rate_limited` error (quota/rate-limit, never worth retrying) —
      advances the pointer (wrapping after the last), and the same call
      retries immediately on the next one.
    - One full pass over every provider is the most a single call makes:
      if every provider fails once, the call stops and raises the last
      error seen, rather than looping a second time.
    - Any other exception (a permanent, provider-agnostic failure) simply
      propagates — every provider would fail it the same way.
    """

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
