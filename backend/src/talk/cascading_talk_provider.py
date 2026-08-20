"""Composite TalkProvider: presents an ordered list of TalkProvider
implementations as a single one — same contract, same exceptions — with
retry-in-place, backoff, ordered fallback and logging happening underneath."""
from __future__ import annotations

from typing import AsyncIterator

from cascade import ProviderCascade, ProviderError, ProviderRateLimitedError, ProviderUnavailableError
from talk.talk_provider import TalkProvider


class CascadingTalkProvider(TalkProvider):
    def __init__(self, providers: list[tuple[str, TalkProvider]]) -> None:
        self._cascade: ProviderCascade[TalkProvider] = ProviderCascade(providers, kind="talk")

    async def generate(self, text: str) -> AsyncIterator[tuple[bytes, int]]:
        """Cascades across every configured provider. Once a provider has
        started yielding chunks, a failure can only cascade to the next
        one, not retry the same one in place — hence this extra loop."""
        last_error: BaseException | None = None
        for _ in range(len(self._cascade)):
            result = await self._cascade.call_with_retry(
                lambda provider: provider.generate(text),
                unavailable=ProviderUnavailableError,
                rate_limited=ProviderRateLimitedError,
            )
            try:
                for chunk in result:
                    yield chunk
                return
            except ProviderError as exc:
                last_error = exc
                self._cascade.advance()

        raise last_error
