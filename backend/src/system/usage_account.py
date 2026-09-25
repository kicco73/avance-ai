from __future__ import annotations

from typing import Any


class UsageAccount(object):
    kind = "unattributed"

    def columns(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "session_id": None, "session_type": None, "turn_id": None, "test_run_id": None,
            "username": None, "project_id": None,
        }


class SessionAccount(UsageAccount):
    kind = "session"

    def __init__(self, session: dict, turn_id: str | None = None) -> None:
        self._session = session
        self._turn_id = turn_id

    def columns(self) -> dict[str, Any]:
        return {
            **super().columns(),
            "session_id": self._session["id"], "session_type": self._session["type"], "turn_id": self._turn_id,
            "username": self._session["username"],
            "project_id": self._session["project_id"],
        }


class ProjectAccount(UsageAccount):
    kind = "project"

    def __init__(self, project_id: str, username: str | None) -> None:
        self._project_id = project_id
        self._username = username

    def columns(self) -> dict[str, Any]:
        return {**super().columns(), "username": self._username, "project_id": self._project_id}


UNATTRIBUTED = UsageAccount()


def charged(ai_service: Any, account: UsageAccount) -> Any:
    return None if ai_service is None else ai_service.charged_to(account)
