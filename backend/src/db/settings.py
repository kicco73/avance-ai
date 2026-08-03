from __future__ import annotations

from .models import DEFAULT_USER, Settings


class SettingsMixin:

    def get_active_project_name(self, user: str=DEFAULT_USER) -> str | None:
        row = Settings.get_or_none(Settings.user == user)
        return row.project if row is not None else None

    def set_active_project_name(self, project_name: str, user: str=DEFAULT_USER) -> None:
        Settings.insert(user=user, project=project_name).on_conflict(conflict_target=[Settings.user], update={Settings.project: project_name}).execute()

    def clear_active_project_name(self, user: str=DEFAULT_USER) -> None:
        Settings.delete().where(Settings.user == user).execute()
