"""The audio (TTS) layer as a single, independent service — same shape
as ai/ai_service.py's AiService, but its own provider roster and its own
cascade.ProviderCascade instance (no code dependency between the two,
even though they may share the same account/key in practice). Built
once in main.py, passed explicitly to whatever needs it (ChatService).
"""
from __future__ import annotations

import logging
from typing import Iterator

from config import AudioServiceConfig
from cascade import ProviderCascade
from audio.audio_provider import AudioProvider, AudioProviderError
from audio.gemini_audio_provider import GeminiAudioProvider
from audio.piper.piper_audio_provider import PiperAudioProvider


class AudioService(object):
    _PROVIDER_CLASSES = {
        "gemini": GeminiAudioProvider,
        "piper": PiperAudioProvider,  # local, no `key` needed
    }
    logger = logging.getLogger(__name__)

    def __init__(self, audio_service_config: list[AudioServiceConfig]) -> None:
        providers = [
            (f"{service.name}/{service.model}", self._build_provider(service))
            for service in audio_service_config
        ]
        self._cascade: ProviderCascade[AudioProvider] = ProviderCascade(providers, kind="audio")

    @classmethod
    def _build_provider(cls, service: AudioServiceConfig) -> AudioProvider:
        if service.name not in cls._PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid audio provider name: {service.name!r}. Must be one of: "
                f"{', '.join(cls._PROVIDER_CLASSES.keys())}"
            )
        return cls._PROVIDER_CLASSES[service.name](api_key=service.key, model=service.model)

    async def generate_audio(self, text: str) -> Iterator[tuple[bytes, int]] | None:
        """Single entry point for audio — callers never see AudioProvider
        or the cascade. Delegates to the current provider's own stream()
        (see audio_provider.py); never raises, returns None on failure."""
        provider = self._cascade.current
        try:
            return await provider.stream(self._cascade, text)
        except AudioProviderError as exc:
            self.logger.warning("Audio generation failed on every configured provider: %s", exc)
            return None
