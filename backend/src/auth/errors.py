"""AuthError: raised by an AuthProvider when a credential fails
verification (expired/tampered/wrong audience) — distinct from an
unrecognized provider name, which AuthController rejects with an
explicit 400 before ever reaching a provider.
"""
from __future__ import annotations

from http import HTTPStatus

from service_error import ServiceError


class AuthError(ServiceError):
    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message, detail, status_code=HTTPStatus.UNAUTHORIZED)
