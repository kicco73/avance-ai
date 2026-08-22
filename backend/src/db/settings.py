from __future__ import annotations

from .models import Settings


class SettingsMixin:

    def get_setting(self, key: str) -> str | None:
        row = Settings.get_or_none(Settings.key == key)
        return row.value if row is not None else None

    def set_setting(self, key: str, value: str) -> None:
        Settings.insert(key=key, value=value).on_conflict(
            conflict_target=[Settings.key], update={Settings.value: value}
        ).execute()
