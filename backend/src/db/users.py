from __future__ import annotations

from datetime import datetime

from peewee import SQL

from .models import Project, User
from .utils import _utc_iso

_ADMIN_EMAILS = {"enrico.carniani@gmail.com", "itinococalero@gmail.com"}


def _initial_role(email: str) -> str:
    return "admin" if email in _ADMIN_EMAILS else "user"


class UserMixin:

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

    def get_or_create_user(self, provider: str, provider_user_id: str, email: str, name: str, picture_url: str | None) -> User:
        user, _ = User.get_or_create(
            id=email,
            defaults={
                "provider": provider, "provider_user_id": provider_user_id, "email": email, "name": name,
                "picture_url": picture_url, "role": _initial_role(email),
            },
        )
        user.name = name
        user.picture_url = picture_url
        user.save()
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

    def get_user_by_email(self, email: str) -> dict | None:
        """AuthService.get_profile's own lookup — GET /api/auth/me's
        source, both for the topbar avatar and ProfileView.vue."""
        user = User.get_or_none(User.email == email)
        if user is None:
            return None
        return {
            "email": user.email,
            "name": user.name,
            "picture_url": user.picture_url,
            "provider": user.provider,
            "role": user.role,
            "created_at": _utc_iso(user.created_at),
            "last_login": _utc_iso(user.last_login),
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

    def update_last_login(self, user_id) -> None:
        User.update(last_login=datetime.utcnow()).where(User.id == user_id).execute()

    def get_active_project_name(self, user: str) -> str | None:
        row = User.get_or_none(User.email == user)
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
        row = User.get_or_none(User.email == user)
        if row is not None:
            row.active_project_id = project_name
            row.save()
        else:
            User.create(id=user, email=user, active_project_id=project_name)

    def clear_active_project_name(self, user: str) -> None:
        User.update(active_project_id=None).where(User.email == user).execute()
