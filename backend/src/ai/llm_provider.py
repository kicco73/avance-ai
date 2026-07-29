"""Abstract interface shared by all LLM providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from cascade import ProviderError, ProviderRateLimitedError, ProviderUnavailableError


class AIServiceError(ProviderError):
    """Readable error to show on the frontend, without crashing the server."""
    message = "AI service error."


class AIServiceProviderUnavailableError(ProviderUnavailableError, AIServiceError):
    """Transient upstream overload (HTTP 503) — worth retrying."""
    message = "AI service unavailable after every retry."


class AIServiceProviderRateLimitedError(ProviderRateLimitedError, AIServiceError):
    """The upstream model API rejected the request for rate limiting (HTTP 429)."""
    message = "The AI service rate limit was exceeded."


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, history: list[dict]) -> str:
        """Returns the reply text for `history` (list of {role, content}).
        Raises AIServiceError on failure, never an unhandled exception."""
        raise NotImplementedError
