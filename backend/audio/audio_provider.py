"""Abstract interface shared by all audio (TTS) providers. Independent
of ai/llm_provider.py's LLMProvider — no shared code.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Iterator

from cascade import ProviderCascade

logger = logging.getLogger(__name__)


class AudioProviderError(Exception):
    """Readable error, without crashing the server."""
    message = "Audio service error."
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    detail = None
    def __init__(self, message: str) -> None:
        self.detail = message


class AudioProviderUnavailableError(AudioProviderError):
    """Transient upstream overload (HTTP 503) — retried with backoff."""
    message = "Audio service unavailable after every retry."
    status_code = HTTPStatus.SERVICE_UNAVAILABLE


class AudioProviderRateLimitedError(AudioProviderError):
    """Rate limit/quota (HTTP 429) — never retried, cascades immediately."""
    message = "The audio service rate limit was exceeded."
    status_code = HTTPStatus.TOO_MANY_REQUESTS


class AudioProvider(ABC):
    @abstractmethod
    def generate_audio(self, text: str) -> Iterator[tuple[bytes, int]]:
        """Yields (raw_pcm_chunk, sample_rate) tuples for `text`. Raises
        AudioProviderError (or a subclass) on failure."""
        raise NotImplementedError


class BufferedAudioProvider(AudioProvider):
    """For a provider whose failures are worth retrying (e.g. a remote
    API like Gemini). stream() materializes the whole utterance inside
    the cascade's retry-with-backoff protection before forwarding any of
    it — costs incremental latency, buys the ability to retry/cascade a
    mid-stream failure."""

    async def stream(self, cascade: ProviderCascade, text: str) -> Iterator[tuple[bytes, int]]:
        chunks = await cascade.call_with_retry(
            lambda provider: list(provider.generate_audio(text)),
            unavailable=AudioProviderUnavailableError,
            rate_limited=AudioProviderRateLimitedError,
        )
        return iter(chunks)


class StreamingAudioProvider(AudioProvider):
    """For a provider with nothing worth retrying (e.g. local/offline
    Piper). stream() forwards chunks as they're produced, no buffering.
    A failure advances the cascade for future calls but isn't retried or
    cascaded within this call — some audio may already be out."""

    async def stream(self, cascade: ProviderCascade, text: str) -> Iterator[tuple[bytes, int]]:
        return self._stream_directly(cascade, text)

    def _stream_directly(self, cascade: ProviderCascade, text: str) -> Iterator[tuple[bytes, int]]:
        try:
            yield from self.generate_audio(text)
        except AudioProviderError as exc:
            logger.warning("Audio generation failed: %s", exc)
            cascade.advance()
