"""Orchestrates login (verify a credential, resolve/create the User row,
issue a session JWT) and token verification (used by the auth middleware
on every subsequent request). Not cascading like ai/ai_service.py's
AiService — the client picks a provider explicitly at login, there's no
automatic fallback between them.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import jwt

from auth.auth_provider import AuthenticatedUser, AuthProvider
from auth.providers.google_auth_provider import GoogleAuthProvider
from config import AuthProviderConfig
from db import Db

_JWT_ALGORITHM = "HS256"

# Shared by AuthController (sets/clears it), the auth middleware, and
# WsAdapter's own handshake check (both read it) — one name, defined once.
SESSION_COOKIE_NAME = "avance_session"

_PROVIDER_CLASSES: dict[str, type[AuthProvider]] = {
    "google": GoogleAuthProvider,
}


class AuthService:
    # Takes the specific config value it needs (AppConfig.auth_providers),
    # not the whole AppConfig object — same shape as AiService.from_config
    # (config.ai_services) elsewhere, and easier to construct from a test
    # without a real config.yml.
    def __init__(self, db: Db, providers: list[AuthProviderConfig], token_ttl_in_hours: float) -> None:
        self._db = db
        self._jwt_secret = self._resolve_jwt_secret()
        self.token_ttl = timedelta(hours=token_ttl_in_hours)
        # Only entries whose driver this build actually knows how to
        # construct a provider for — config.py's own parsing doesn't
        # restrict `driver` to a known set (see AppConfig._parse_auth_providers).
        self._providers: dict[str, AuthProvider] = {
            entry.driver: _PROVIDER_CLASSES[entry.driver](entry.key)
            for entry in providers
            if entry.driver in _PROVIDER_CLASSES
        }

    def _resolve_jwt_secret(self) -> str:
        secret = self._db.get_setting("jwt-secret")
        if secret is None:
            secret = secrets.token_hex(32)
            self._db.set_setting("jwt-secret", secret)
        return secret

    def public_providers(self) -> list[dict]:
        return [{"driver": driver, **provider.public_config()} for driver, provider in self._providers.items()]

    def login(self, provider: str, credential: str) -> str:
        """Raises ValueError for an unrecognized `provider` name (the
        controller turns that into an explicit 400) or AuthError if the
        credential itself fails verification."""
        auth_provider = self._providers.get(provider)
        if auth_provider is None:
            raise ValueError(f"Unknown auth provider: {provider!r}.")

        identity = auth_provider.verify(credential)
        user = self._db.get_or_create_user(
            provider, identity.provider_user_id, identity.email, identity.name, identity.picture_url
        )
        self._db.update_last_login(user.id)
        return self._issue_token(user.id, provider)

    def _issue_token(self, user_id: str, provider: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {"user_id": user_id, "provider": provider, "exp": now + self.token_ttl}
        return jwt.encode(payload, self._jwt_secret, algorithm=_JWT_ALGORITHM)

    def verify_token(self, token: str) -> AuthenticatedUser | None:
        """None for anything wrong with `token` — expired, tampered, or
        naming a user_id that no longer exists — never raises: the
        middleware treats None the same as "no cookie at all" (401)."""
        try:
            payload = jwt.decode(token, self._jwt_secret, algorithms=[_JWT_ALGORITHM])
        except jwt.PyJWTError:
            return None

        user = self._db.get_user_by_id(payload.get("user_id"))
        if user is None:
            return None
        return AuthenticatedUser(
            provider_user_id=user["provider_user_id"], email=user["email"], name=user["name"],
            picture_url=user["picture_url"],
        )

    def get_profile(self, email: str) -> dict | None:
        return self._db.get_user_by_email(email)
