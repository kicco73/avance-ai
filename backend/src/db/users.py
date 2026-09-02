from __future__ import annotations

from datetime import datetime
from typing import Any

from peewee import SQL, IntegrityError

from .models import Project, User
from .utils import _utc_iso

_ADMIN_EMAILS = {"enrico.carniani@gmail.com", "itinococalero@gmail.com"}


def _initial_role(email: str | None) -> str:
    return "admin" if email in _ADMIN_EMAILS else "user"


class UserMixin:

    def is_pre_wired_admin(self, email: str) -> bool:
        return email in _ADMIN_EMAILS

    def resolve_login(self, provider: str, provider_user_id: str, email: str, name: str, picture_url: str | None) -> None:
        """AuthService.login's single touchpoint for the User row.
        Syncs name/picture_url/last_login for an already-registered
        account; a no-op for anyone without a row yet — pre-wired admin
        addresses (_ADMIN_EMAILS) included, so they still land on
        role=None and go through TermsView.vue like every other
        first-time identity instead of skipping Terms acceptance.
        AuthService.complete_registration is what actually creates the
        row, waiving the invite requirement for those two addresses
        specifically since no one exists yet to invite either of them."""
        if User.get_or_none(User.id == email) is None:
            return
        self.update_last_login(email, name, picture_url)

    def list_users(self) -> list[dict]:
        return [
            {
                "id": user.id,
                "provider": user.provider,
                "email": user.email,
                "name": user.name,
                "picture_url": user.picture_url,
                "role": user.role,
                "created_at": _utc_iso(user.created_at),
                "last_login": _utc_iso(user.last_login),
            }
            for user in User.select().order_by(User.created_at.asc())
        ]

    def get_or_create_user(
        self, provider: str | None, provider_user_id: str | None, email: str | None,
        name: str | None, picture_url: str | None, user_id: str | None = None,
    ) -> User:
        user, _ = User.get_or_create(
            id=user_id or email,
            defaults={
                "provider": provider, "provider_user_id": provider_user_id, "email": email, "name": name,
                "picture_url": picture_url, "role": _initial_role(email),
            },
        )
        return user

    def get_user_by_id(self, user_id: str) -> dict | None:
        """AuthService.verify_token's own lookup: the JWT payload only
        carries user_id (the user's email), so the identity it needs back
        out (provider_user_id/email/name/picture_url) has to come from here."""
        user = User.get_or_none(User.id == user_id)
        if user is None:
            return None
        return {
            "id": user.id,
            "provider": user.provider,
            "provider_user_id": user.provider_user_id,
            "email": user.email,
            "name": user.name,
            "picture_url": user.picture_url,
            "role": user.role,
            "last_login": user.last_login,
        }

    def get_user_by_whatsapp_phone_number(self, whatsapp_phone_number: str) -> dict | None:
        user = User.get_or_none(User.whatsapp_phone_number == whatsapp_phone_number)
        if user is None:
            return None
        return {
            "id": user.id,
            "provider": user.provider,
            "provider_user_id": user.provider_user_id,
            "email": user.email,
            "name": user.name,
            "picture_url": user.picture_url,
            "role": user.role,
            "last_login": user.last_login,
        }

    def get_user_by_email(self, email: str) -> dict | None:
        """AuthService.get_profile's own lookup — GET /api/auth/me's
        source, both for the topbar avatar and ProfileView.vue."""
        user = User.get_or_none(User.id == email)
        if user is None:
            return None
        return {
            "email": user.email,
            "name": user.name,
            "picture_url": user.picture_url,
            "provider": user.provider,
            "role": user.role,
            "whatsapp_phone_number": user.whatsapp_phone_number,
            "created_at": _utc_iso(user.created_at),
            "last_login": _utc_iso(user.last_login),
        }

    def set_whatsapp_phone_number(self, email: str, whatsapp_phone_number: str | None) -> None:
        try:
            User.update(whatsapp_phone_number=whatsapp_phone_number).where(User.id == email).execute()
        except IntegrityError as exc:
            raise ValueError("This WhatsApp number is already linked to another account.") from exc

    def get_user_facts(self, email: str) -> dict[str, Any]:
        """tracking.user_facts.UserFacts's own source — every User field
        except id, keyed exactly as a trigger/env: expression references
        them (user.email, user.name, ...; user.active_project resolves
        to the project's own name string, same as active_project_id
        everywhere else in this module). {} for an identity with no
        User row yet, rather than raising — the same "just missing"
        shape env.* namespace values get from PersistedEnv."""
        user = User.get_or_none(User.id == email)
        if user is None:
            return {}
        return {
            "provider": user.provider,
            "provider_user_id": user.provider_user_id,
            "email": user.email,
            "name": user.name,
            "picture_url": user.picture_url,
            "created_at": _utc_iso(user.created_at),
            "last_login": _utc_iso(user.last_login),
            "active_project": user.active_project_id,
            "role": user.role,
        }

    def set_user_role(self, user_id: str, role: str) -> None:
        User.update(role=role).where(User.id == user_id).execute()

    def erase_user_data(self, email: str) -> None:
        """ProfileView.vue's "Erase all my data" — deleting the User row
        is now enough on its own: ChatSession.user/Test.user/
        SystemWarning.user_id/EditHistory.user_id are real FKs onto it with
        on_delete='CASCADE' (see models.py), which in turn cascades
        further to Message/Tracking/SessionSummary/TestObservation
        via their own existing FKs onto ChatSession/Test.

        Deleting the User row last would matter if anything above still
        needed to look it up mid-delete — nothing does, so this is just
        the one statement. Next login re-authenticates as a brand new,
        unregistered identity (see auth_service.py's own login/
        verify_token), routing straight back through TermsView.vue."""
        User.delete().where(User.id == email).execute()

    def update_last_login(self, user_id: str, name: str, picture_url: str | None) -> None:
        """Called on every login (see AuthService.login/complete_registration)
        with the identity the provider just verified — refreshes
        name/picture_url alongside the timestamp, so profile data set at
        registration time catches up to the provider's current values
        the next time that account actually logs in."""
        User.update(name=name, picture_url=picture_url, last_login=datetime.utcnow()).where(User.id == user_id).execute()

    def get_active_project_name(self, user: str) -> str | None:
        row = User.get_or_none(User.id == user)
        if row is None:
            return None
        if row.active_project_id is None:
            first_project = Project.select(Project.name).order_by(SQL('rowid')).first()
            if first_project is None:
                return None
            row.active_project_id = first_project.name
            row.save()
        return row.active_project_id

    def set_active_project_name(self, project_name: str, user: str) -> None:
        row = User.get_or_none(User.id == user)
        if row is not None:
            row.active_project_id = project_name
            row.save()
        else:
            User.create(id=user, active_project_id=project_name)

    def clear_active_project_name(self, user: str) -> None:
        User.update(active_project_id=None).where(User.id == user).execute()
