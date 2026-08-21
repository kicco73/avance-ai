"""Verifies a Google Identity Services ID token client-side "Sign in with
Google" hands the frontend — signature, expiry, and audience (our own
Google OAuth client id) are all checked by the official google-auth
library itself, never reimplemented here.
"""
from __future__ import annotations

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from auth.auth_provider import AuthenticatedUser, AuthProvider
from auth.errors import AuthError


class GoogleAuthProvider(AuthProvider):
    def __init__(self, client_id: str) -> None:
        self._client_id = client_id

    def verify(self, credential: str) -> AuthenticatedUser:
        try:
            payload = id_token.verify_oauth2_token(
                credential, google_requests.Request(), self._client_id
            )
        except ValueError as exc:
            raise AuthError(f"Invalid Google credential: {exc}") from exc

        return AuthenticatedUser(
            provider_user_id=payload["sub"],
            email=payload["email"],
            name=payload.get("name", payload["email"]),
        )

    def public_config(self) -> dict:
        return {"client_id": self._client_id}
