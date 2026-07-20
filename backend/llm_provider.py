"""Abstract interface shared by all LLM providers.

`main.py` depends only on this module: it must never import `anthropic`
or `google.genai` directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProviderError(Exception):
    """Readable error to show on the frontend, without crashing the server."""


class LLMProviderUnavailableError(LLMProviderError):
    """The upstream model API is temporarily overloaded/unavailable (HTTP 503).

    Kept distinct from LLMProviderError so callers can tell a transient,
    worth-retrying failure apart from a permanent one.
    """


class LLMProviderRateLimitedError(LLMProviderError):
    """The upstream model API rejected the request for rate limiting (HTTP 429)."""


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
