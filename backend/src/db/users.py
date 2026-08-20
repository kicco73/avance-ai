from __future__ import annotations

from datetime import datetime

from .models import DEFAULT_USER, User


class UserMixin:

    def get_or_create_user(self, provider: str, provider_user_id: str, email: str, name: str) -> User:
        user, _ = User.get_or_create(
            provider=provider, provider_user_id=provider_user_id,
            defaults={"email": email, "name": name},
        )
        return user

    def get_user_by_id(self, user_id: int) -> dict | None:
        """AuthService.verify_token's own lookup: the JWT payload only
        carries user_id, so the identity it needs back out (provider_
        user_id/email/name) has to come from here."""
        user = User.get_or_none(User.id == user_id)
        if user is None:
            return None
        return {
            "id": user.id,
            "provider": user.provider,
            "provider_user_id": user.provider_user_id,
            "email": user.email,
            "name": user.name,
            "last_login": user.last_login,
        }

    def update_last_login(self, user_id) -> None:
        User.update(last_login=datetime.utcnow()).where(User.id == user_id).execute()

    def get_active_project_name(self, user: str = DEFAULT_USER) -> str | None:
        row = User.get_or_none(User.email == user)
        return row.active_project if row is not None else None

    def set_active_project_name(self, project_name: str, user: str = DEFAULT_USER) -> None:
        row = User.get_or_none(User.email == user)
        if row is not None:
            row.active_project = project_name
            row.save()
        else:
            User.create(email=user, active_project=project_name)

    def clear_active_project_name(self, user: str = DEFAULT_USER) -> None:
        User.update(active_project=None).where(User.email == user).execute()
