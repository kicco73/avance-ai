"""The audio (TTS) layer as a single, independent service — same
architectural shape as ai/ai_service.py's AiService, but with its own
provider roster and its own cascade.ProviderCascade instance: no code
dependency between the two modules, even though in practice they may
share the same underlying account/key (see AppConfig.audio_services vs.
ai_services). Constructed once in main.py and passed explicitly to
whatever needs it (ChatService).
"""
from __future__ import annotations

import logging
from typing import Iterator

from config import AudioServiceConfig
from cascade import ProviderCascade
from audio.audio_provider import (
    AudioProvider,
    AudioProviderError,
    AudioProviderRateLimitedError,
    AudioProviderUnavailableError,
)
from audio.gemini_audio_provider import GeminiAudioProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES = {
    "gemini": GeminiAudioProvider,
    # A future local provider needing no `key` (e.g. Piper) goes here too
    # — see AudioServiceConfig.key being optional for exactly this.
}


class AudioService(object):
    def __init__(self, audio_service_config: list[AudioServiceConfig]) -> None:
        providers = [
            (f"{service.name}/{service.model}", self._build_provider(service))
            for service in audio_service_config
        ]
        self._cascade: ProviderCascade[AudioProvider] = ProviderCascade(providers, kind="audio")

    @staticmethod
    def _build_provider(service: AudioServiceConfig) -> AudioProvider:
        if service.name not in _PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid audio provider name: {service.name!r}. Must be one of: "
                f"{', '.join(_PROVIDER_CLASSES.keys())}"
            )
        return _PROVIDER_CLASSES[service.name](api_key=service.key, model=service.model)

    async def generate_audio(self, text: str) -> Iterator[tuple[bytes, int]] | None:
        """The single entry point the rest of the backend uses for audio
        — callers never see AudioProvider, GeminiAudioProvider, or the
        cascade itself. Cascades across every configured provider the
        same way AiService.generate() does for text (see
        cascade.ProviderCascade): a transient failure is retried in
        place first, an outright unavailable provider (that retry
        exhausted, or a rate-limit/quota error) advances to the next one
        immediately.

        Unlike AiService.generate(), never raises: audio is a
        supplementary feature, not worth failing a chat turn over (same
        tolerance the old LLMProvider.generate_audio_stream had) —
        returns None if every provider in one full pass failed.

        Materializes the whole stream inside the cascade's retry-
        protected call (rather than yielding incrementally as chunks
        arrive from the network), so a mid-stream failure can still be
        retried/cascaded like any other failure — trading true
        incremental low-latency streaming for that safety. The caller
        still gets back an iterator of the same (chunk, sample_rate)
        shape as before."""
        try:
            chunks = await self._cascade.call_with_retry(
                lambda provider: list(provider.generate_audio(text)),
                unavailable=AudioProviderUnavailableError,
                rate_limited=AudioProviderRateLimitedError,
            )
        except AudioProviderError as exc:
            logger.warning("Audio generation failed on every configured provider: %s", exc)
            return None
        return iter(chunks)
