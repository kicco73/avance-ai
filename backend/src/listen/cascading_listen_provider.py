"""Composite ListenProvider: presents an ordered list of ListenProvider
implementations as a single one — same contract, same exceptions — with
retry-in-place, backoff, ordered fallback and logging (see cascade.py's
ProviderCascade) happening underneath. Anywhere a ListenProvider is
expected, this is a legal, swappable value — see listen_service.py's
ListenService.
"""
from __future__ import annotations

from cascade import ProviderCascade, ProviderRateLimitedError, ProviderUnavailableError
from listen.listen_provider import ListenProvider


class CascadingListenProvider(ListenProvider):
    def __init__(self, providers: list[tuple[str, ListenProvider]]) -> None:
        self._cascade: ProviderCascade[ListenProvider] = ProviderCascade(providers, kind="listen")

    async def transcribe(self, audio: bytes) -> str:
        return await self._cascade.call_with_retry(
            lambda provider: provider.transcribe(audio),
            unavailable=ProviderUnavailableError,
            rate_limited=ProviderRateLimitedError,
        )
