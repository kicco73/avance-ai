"""Orchestrates login (verify a credential, resolve/create the User row,
issue a session JWT) and token verification (used by the auth middleware
on every subsequent request). Not cascading like ai/ai_service.py's
AiService — the client picks a provider explicitly at login, there's no
automatic fallback between them.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import jwt

from auth.auth_provider import AuthenticatedUser, AuthProvider
from auth.providers.google_auth_provider import GoogleAuthProvider
from config import AuthProviderConfig
from db import Db

if TYPE_CHECKING:
    from project.project_service import ProjectService

_JWT_ALGORITHM = "HS256"

# Shared by AuthController (sets/clears it), the auth middleware, and
# WsAdapter's own handshake check (both read it) — one name, defined once.
SESSION_COOKIE_NAME = "avance_session"

_PROVIDER_CLASSES: dict[str, type[AuthProvider]] = {
    "google": GoogleAuthProvider,
}


class AuthService:
    # Takes the specific config value it needs (AppConfig.auth_providers),
    # not the whole AppConfig object — same shape as AiService.for_live
    # (config.ai_services) elsewhere, and easier to construct from a test
    # without a real config.yml.
    def __init__(
        self, db: Db, providers: list[AuthProviderConfig], token_ttl_in_hours: float,
        project_service: "ProjectService",
    ) -> None:
        self._db = db
        self._project_service = project_service
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
        credential itself fails verification.

        Deliberately never creates the User row here for a first-time
        identity — it gets a session token straight off the verified
        identity (see _issue_token) and stays unregistered until
        TermsView.vue's Accept action calls complete_registration().
        Rejecting Terms then leaves zero trace: no row was ever created
        to clean up. This holds even for the two pre-wired admin
        addresses (Db.is_pre_wired_admin) — they still see and accept
        Terms like anyone else, just without needing an invite code."""
        auth_provider = self._providers.get(provider)
        if auth_provider is None:
            raise ValueError(f"Unknown auth provider: {provider!r}.")

        identity = auth_provider.verify(credential)
        self._db.resolve_login(provider, identity.provider_user_id, identity.email, identity.name, identity.picture_url)
        return self._issue_token(identity, provider)

    def _issue_token(self, identity: AuthenticatedUser, provider: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "email": identity.email,
            "provider_user_id": identity.provider_user_id,
            "name": identity.name,
            "picture_url": identity.picture_url,
            "provider": provider,
            "exp": now + self.token_ttl,
        }
        return jwt.encode(payload, self._jwt_secret, algorithm=_JWT_ALGORITHM)

    def _decode(self, token: str) -> dict | None:
        try:
            return jwt.decode(token, self._jwt_secret, algorithms=[_JWT_ALGORITHM])
        except jwt.PyJWTError:
            return None

    def verify_token(self, token: str) -> AuthenticatedUser | None:
        """None for anything wrong with `token` itself — expired or
        tampered — never raises: the middleware treats None the same as
        "no cookie at all" (401).

        A token that decodes fine but names an identity with no User row
        yet (mid Terms-of-Service flow) resolves straight off the token's
        own payload instead, with role=None: role_satisfies() then blocks
        every real route for it (only the "pending"-tier ones — accept-
        terms, logout — stay reachable), which is exactly the gate
        pingBackend() in App.vue relies on (a 403 off GET /api/state)."""
        payload = self._decode(token)
        email = payload.get("email") if payload else None
        # Also catches a still-valid-signature token issued before this
        # payload shape existed (the old {"user_id": ...} one) — treated
        # as invalid rather than crashing on a missing key.
        if email is None:
            return None

        user = self._db.get_user_by_id(email)
        if user is not None:
            return AuthenticatedUser(
                provider_user_id=user["provider_user_id"], email=user["email"], name=user["name"],
                picture_url=user["picture_url"], role=user["role"],
            )
        return AuthenticatedUser(
            provider_user_id=payload.get("provider_user_id"), email=email, name=payload.get("name"),
            picture_url=payload.get("picture_url"), role=None,
        )

    def _register_with_invite(
        self, user_id: str, provider: str | None, provider_user_id: str | None,
        email: str | None, name: str | None, picture_url: str | None,
        invite_code: str | None, invite_exempt: bool,
    ):
        """Shared by complete_registration (web) and register_via_whatsapp:
        self-registration is invite-only — `invite_code` must clear
        ProjectService.validate_invite_for_registration (exists, not
        expired, under its max-shares budget), or this is a stranger who
        was never invited, registration refused, no User row created (see
        that method for the specific PermissionError each failure raises).
        Invite validation happens before the User row is ever created, and
        its redemption is only recorded (see ProjectService.redeem_invite)
        once that row actually exists. `invite_exempt=True` (the two
        pre-wired admin addresses, web-only) skips straight to row
        creation instead — no invite exists to redeem there since no one
        exists yet to send them one."""
        invite = None
        if not invite_exempt:
            invite = self._project_service.validate_invite_for_registration(invite_code)
        user = self._db.get_or_create_user(provider, provider_user_id, email, name, picture_url, user_id=user_id)
        self._db.update_last_login(user.id, name, picture_url)
        if invite is not None:
            self._project_service.redeem_invite(invite, user.id)
        return user, invite

    def complete_registration(self, token: str, invite_code: str | None = None) -> None:
        """TermsView.vue's Accept action: creates the User row login()
        deliberately deferred, keyed off the same already-issued token —
        no new cookie needed, verify_token resolves it as a normal
        registered user from here on. See _register_with_invite for the
        invite-redemption rules this delegates to."""
        payload = self._decode(token)
        email = payload.get("email") if payload else None
        if email is None:
            raise ValueError("Invalid or expired session.")
        self._register_with_invite(
            email, payload.get("provider"), payload.get("provider_user_id"), email,
            payload.get("name"), payload.get("picture_url"),
            invite_code, invite_exempt=self._db.is_pre_wired_admin(email),
        )

    def register_via_whatsapp(self, phone_number: str, invite_code: str) -> str:
        """WhatsAppService's own registration path (see
        whatsapp_service.py): the invite code IS the inbound message text
        from a number with no linked account at all. Same invite rules as
        the web path (_register_with_invite), no pre-wired-admin exemption
        here — every WhatsApp signup needs a real invite. `id` is the
        phone number itself (provider="whatsapp"); every Google-only field
        (email/name/picture_url/provider_user_id) stays unset. Returns the
        project the invite granted access to, set as this brand-new
        account's active project directly — unlike the web, there's no
        later "activate" step for a WhatsApp identity to go through (see
        useAppBoot.js's activateInvitedProject)."""
        _user, invite = self._register_with_invite(
            phone_number, "whatsapp", None, None, None, None, invite_code, invite_exempt=False,
        )
        self._db.set_whatsapp_phone_number(phone_number, phone_number)
        self._db.set_active_project_name(invite.project_name_id, phone_number)
        return invite.project_name_id

    def is_invite_exempt(self, email: str) -> bool:
        """App.vue's own TermsView-vs-InviteRequiredView gate for a
        pending (role=None) identity: the same exemption
        complete_registration already grants the two pre-wired admin
        addresses, surfaced here so the frontend can pick TermsView for
        them too even with no "share project" invite link in the URL —
        e.g. after "Erase all my data" wiped their User row and a plain
        re-login (no invite link involved) leaves them pending again."""
        return self._db.is_pre_wired_admin(email)

    def get_profile(self, email: str) -> dict | None:
        return self._db.get_user_by_email(email)

    def set_whatsapp_phone_number(self, email: str, phone_number: str | None, confirm_merge: bool = False) -> dict | None:
        normalized = None
        if phone_number is not None and phone_number.strip():
            normalized = phone_number.strip().lstrip("+")
            if not normalized.isdigit():
                raise ValueError("WhatsApp phone number must be digits only (E.164, no '+'), e.g. 34600000001.")
        if normalized is not None:
            existing = self._db.get_user_by_whatsapp_phone_number(normalized)
            if existing is not None and existing["id"] != email:
                if existing["provider"] != "whatsapp":
                    raise ValueError("This WhatsApp number is already linked to another account.")
                if not confirm_merge:
                    return {
                        "merge_required": True,
                        "existing_account_created_at": existing["created_at"],
                        "existing_account_session_count": self._db.count_sessions_for_user(existing["id"]),
                    }
                self._db.merge_whatsapp_account(email, existing["id"], normalized)
                return self._db.get_user_by_email(email)
        self._db.set_whatsapp_phone_number(email, normalized)
        return self._db.get_user_by_email(email)

    def erase_account(self, email: str) -> None:
        """ProfileView.vue's "Erase all my data" — see Db.erase_user_data
        for what actually gets deleted and why it's done by value rather
        than a DB-level FK cascade."""
        self._db.erase_user_data(email)

    def list_users(self) -> list[dict]:
        return self._db.list_users()

    def set_user_role(self, user_id: str, role: str) -> dict | None:
        """user_id is the user's own email (see db/users.py's own id=email
        convention), so get_user_by_email returns the same row just updated."""
        self._db.set_user_role(user_id, role)
        return self._db.get_user_by_email(user_id)
