"""Abstract interface shared by all LLM providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class AIServiceError(Exception):
    """Readable error to show on the frontend, without crashing the server."""
    message = f"AI service error."
    status_code = 503
    detail = None
    def __init__(self, message: str) -> None:
        self.detail = message

class AIServiceProviderUnavailableError(AIServiceError):
    """The upstream model API is temporarily overloaded/unavailable (HTTP 503).

    Kept distinct from LLMProviderError so callers can tell a transient,
    worth-retrying failure apart from a permanent one.
    """
    message = "AI service unavailable after every retry."
    status_code = 503


class AIServiceProviderRateLimitedError(AIServiceError):
    """The upstream model API rejected the request for rate limiting (HTTP 429)."""
    message = "The AI service rate limit was exceeded."
    status_code = 429


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, history: list[dict]) -> str:
        """Generates the assistant's reply given the conversation history.

        `history` is a list of {"role": "user"|"assistant", "content": str}.
        Returns the reply text.
        Raises LLMProviderError with a readable message on failure
        (missing key, timeout, API error), without propagating unhandled exceptions.
        """
        raise NotImplementedError

    def generate_audio_stream(self, text: str) -> Iterator[tuple[bytes, int]] | None:
        """Generates spoken audio for `text` if this provider supports
        native audio output, yielding (raw_pcm_chunk, sample_rate) tuples
        as they're produced — or returning None outright if this provider
        doesn't support audio at all. Never raises: audio is a
        supplementary feature (see ChatService), not worth failing a chat
        turn over — a failure partway through just ends the iteration
        early, same tolerance as returning None up front. Base
        implementation: no provider supports this unless it overrides the
        method (e.g. GeminiProvider) — Anthropic and any future
        non-audio-capable provider get this no-op for free.

        Note this is a plain function, not a generator (no `yield`
        anywhere in this base body) — calling it therefore really does
        return None immediately, rather than a generator object that
        would raise StopIteration on first use. A subclass overriding it
        with an actual generator (`yield`ing chunks) is never confused
        with "unsupported": the two are distinguished by identity (is the
        return value None, or an iterator), not by both trying to look
        like empty iterators."""
        return None
