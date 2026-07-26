"""Abstract interface shared by all audio (TTS) providers. Same principle
as ai/llm_provider.py's LLMProvider — but deliberately independent of it,
no shared code: see audio_service.py's module docstring.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class AudioProviderError(Exception):
    """Readable error, without crashing the server."""
    message = "Audio service error."
    status_code = 503
    detail = None
    def __init__(self, message: str) -> None:
        self.detail = message


class AudioProviderUnavailableError(AudioProviderError):
    """The upstream audio API is temporarily overloaded/unavailable (HTTP
    503) — retried with backoff before the cascade gives up on it.

    Kept distinct from AudioProviderError so callers can tell a transient,
    worth-retrying failure apart from a permanent one.
    """
    message = "Audio service unavailable after every retry."
    status_code = 503


class AudioProviderRateLimitedError(AudioProviderError):
    """The upstream audio API rejected the request for rate limiting (HTTP
    429) — never retried, cascades to the next provider immediately."""
    message = "The audio service rate limit was exceeded."
    status_code = 429


class AudioProvider(ABC):
    @abstractmethod
    def generate_audio(self, text: str) -> Iterator[tuple[bytes, int]]:
        """Generates spoken audio for `text`, yielding (raw_pcm_chunk,
        sample_rate) tuples as they're produced. Raises AudioProviderError
        (or a subclass — see AudioProviderUnavailableError/
        AudioProviderRateLimitedError) on failure rather than swallowing
        it: AudioService's cascade is what decides whether/how to react,
        not the provider itself."""
        raise NotImplementedError
