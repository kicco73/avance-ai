"""Shared HTTP-shaped exception base ({message, detail, status_code}) that
every service-layer error inherits from. error_handlers.py registers one
handler for ServiceError itself; every subclass is covered via MRO.
"""
from __future__ import annotations

from http import HTTPStatus


class ServiceError(Exception):
    def __init__(
        self, message: str, detail: str | None = None, *,
        status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR, code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code
        self.code = code
