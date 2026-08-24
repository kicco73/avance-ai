from __future__ import annotations

from datetime import datetime

from .models import User
from .utils import _utc_iso

#FIXME temporary for prototype

def _initial_role(name: str | None) -> str:
    return 'admin'
    first = (name or "").strip().split()
    first_name = first[0].lower() if first else ""
    if first_name.startswith('i'):
        return "supervisor"
    if first_name == "enrico":
        return "admin"
    return "user"


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
                "picture_url": picture_url, "role": _initial_role(name),
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

    def update_last_login(self, user_id) -> None:
        User.update(last_login=datetime.utcnow()).where(User.id == user_id).execute()

    def get_active_project_name(self, user: str) -> str | None:
        row = User.get_or_none(User.email == user)
        return row.active_project if row is not None else None

    def set_active_project_name(self, project_name: str, user: str) -> None:
        row = User.get_or_none(User.email == user)
        if row is not None:
            row.active_project = project_name
            row.save()
        else:
            User.create(id=user, email=user, active_project=project_name)

    def clear_active_project_name(self, user: str) -> None:
        User.update(active_project=None).where(User.email == user).execute()
