"""Abstract interface shared by all LLM providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


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
