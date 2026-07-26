"""Abstract interface shared by all LLM providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from http import HTTPStatus


class AIServiceError(Exception):
    """Readable error to show on the frontend, without crashing the server."""
    message = f"AI service error."
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    detail = None
    def __init__(self, message: str) -> None:
        self.detail = message

class AIServiceProviderUnavailableError(AIServiceError):
    """Transient upstream overload (HTTP 503) — worth retrying."""
    message = "AI service unavailable after every retry."
    status_code = HTTPStatus.SERVICE_UNAVAILABLE


class AIServiceProviderRateLimitedError(AIServiceError):
    """The upstream model API rejected the request for rate limiting (HTTP 429)."""
    message = "The AI service rate limit was exceeded."
    status_code = HTTPStatus.TOO_MANY_REQUESTS


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, history: list[dict]) -> str:
        """Returns the reply text for `history` (list of {role, content}).
        Raises AIServiceError on failure, never an unhandled exception."""
        raise NotImplementedError
