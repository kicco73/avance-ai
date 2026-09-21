"""The `user` namespace a trigger/`env:` expression resolves against —
every field of the current session's own User row (see db/models.py),
except `id`. Reads WebSession().user lazily, same as PersistedEnv/
SessionFacts, so WebSession().impersonate(...) scopes it to the user
being acted for rather than whoever's live right now."""
from __future__ import annotations

from typing import Any, Protocol

from system.web_session import WebSession


class UserRecords(Protocol):
    def get_user_facts(self, email: str) -> dict[str, Any]: ...


class UserFacts(object):
    def __init__(self, db: "UserRecords") -> None:
        self._db = db

    def as_dict(self) -> dict[str, Any]:
        return self._db.get_user_facts(WebSession().user)
