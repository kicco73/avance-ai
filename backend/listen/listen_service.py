"""The STT layer as a single, independent service — same shape as
talk/talk_service.py's TalkService: from a caller's point of view,
calling transcribe() looks like calling a single provider, cascading
across every configured provider hidden inside.
"""
from __future__ import annotations

from config import ListenServiceConfig
from cascade import ProviderCascade, ProviderRateLimitedError, ProviderUnavailableError
from listen.listen_provider import ListenProvider
from listen.faster_whisper_provider import FasterWhisperProvider


class ListenServiceError(Exception):
    """Raised once every configured STT provider has failed — carries
    the last provider-specific error as __cause__, never leaks it directly."""


class ListenService(ListenProvider):
    _PROVIDER_CLASSES = {
        "faster-whisper": FasterWhisperProvider,
    }

    def __init__(self, listen_service_config: list[ListenServiceConfig]) -> None:
        providers = [
            (f"{service.name}/{service.model}", self._build_provider(service))
            for service in listen_service_config
        ]
        self._cascade: ProviderCascade[ListenProvider] = ProviderCascade(providers, kind="listen")

    @classmethod
    def _build_provider(cls, service: ListenServiceConfig) -> ListenProvider:
        if service.name not in cls._PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid listen provider name: {service.name!r}. Must be one of: "
                f"{', '.join(cls._PROVIDER_CLASSES.keys())}"
            )
        return cls._PROVIDER_CLASSES[service.name](
            api_key=service.key, model=service.model, language=service.language
        )

    async def transcribe(self, audio: bytes) -> str:
        """Transcribed text for `audio`, cascading across every configured
        STT provider. Raises ListenServiceError if all of them fail."""
        try:
            return await self._cascade.call_with_retry(
                lambda provider: provider.transcribe(audio),
                unavailable=ProviderUnavailableError,
                rate_limited=ProviderRateLimitedError,
            )
        except (ProviderUnavailableError, ProviderRateLimitedError) as exc:
            raise ListenServiceError("Every configured STT provider failed.") from exc
