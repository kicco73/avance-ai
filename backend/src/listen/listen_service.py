"""The STT layer as a single, independent service — same shape as
talk/talk_service.py's TalkService: from a caller's point of view,
calling transcribe() looks like calling a single provider, cascading
across every configured provider hidden inside.
"""
from __future__ import annotations

from config import ListenServiceConfig
from cascade import ProviderError
from listen.listen_provider import ListenProvider
from listen.cascading_listen_provider import CascadingListenProvider
from listen.faster_whisper_provider import FasterWhisperProvider


class ListenServiceError(Exception):
    """Raised once every configured STT provider has failed — carries
    the last provider-specific error as __cause__, never leaks it directly."""


class ListenServiceNotAvailableError(Exception):
    """Raised by the controller when listen-service.enabled is false in
    .config.yml — there is no ListenService instance to call at all."""

    def __init__(self, message: str = "Speech-to-text is not enabled on this server.") -> None:
        super().__init__(message)


class ListenService(ListenProvider):
    """Thin wrapper around whatever ListenProvider it's given — a single
    concrete provider or a CascadingListenProvider fronting several, the
    service itself doesn't care which (see cascading_listen_provider.py).
    `from_config` is the usual entry point (see main.py); the plain
    constructor is what makes that substitution possible."""

    _PROVIDER_CLASSES = {
        "faster-whisper": FasterWhisperProvider,
    }

    def __init__(self, provider: ListenProvider) -> None:
        self._provider = provider

    @classmethod
    def from_config(cls, listen_service_config: list[ListenServiceConfig]) -> "ListenService":
        providers = [
            (f"{service.driver}/{service.model}", cls._build_provider(service))
            for service in listen_service_config
        ]
        return cls(CascadingListenProvider(providers))

    @classmethod
    def _build_provider(cls, service: ListenServiceConfig) -> ListenProvider:
        if service.driver not in cls._PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid listen provider driver: {service.driver!r}. Must be one of: "
                f"{', '.join(cls._PROVIDER_CLASSES.keys())}"
            )
        return cls._PROVIDER_CLASSES[service.driver](
            api_key=service.key, model=service.model, language=service.language
        )

    async def transcribe(self, audio: bytes) -> str:
        """Transcribed text for `audio`. Raises ListenServiceError if the
        underlying provider fails — per ListenProvider's own contract,
        that's any cascade.ProviderError, not just the unavailable/rate-
        limited subclasses that trigger a cascade's own retry/fallback."""
        try:
            return await self._provider.transcribe(audio)
        except ProviderError as exc:
            raise ListenServiceError("Every configured STT provider failed.") from exc
