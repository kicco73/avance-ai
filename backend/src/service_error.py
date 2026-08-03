"""Shared HTTP-shaped exception base — {message, detail, status_code} —
every service-layer error (chat.chat_service.ChatServiceError, tracking.
tracking_service.TrackingServiceError, ...) inherits from. error_handlers.py
registers exactly one handler, for ServiceError itself, that covers every
subclass automatically (Starlette resolves an exception handler by
walking the raised exception's own MRO, not by exact type match) —
letting each service layer define its own narrowly-named subclass
without error_handlers.py needing a matching handler for each one.
"""
from __future__ import annotations

from http import HTTPStatus


class ServiceError(Exception):
    def __init__(
        self, message: str, detail: str | None = None, *, status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code
