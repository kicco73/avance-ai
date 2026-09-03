"""The `user` namespace a trigger/`env:` expression resolves against —
every field of the current session's own User row (see db/models.py),
except `id`. Reads Session().user lazily, same as PersistedEnv/
SessionFacts, so Session().impersonate(...) (wakeup_service.py)
scopes it to the user being woken rather than whoever's live right now."""
from __future__ import annotations

from typing import Any

from db import Db
from session import Session


class UserFacts(object):
    def __init__(self, db: Db) -> None:
        self._db = db

    def as_dict(self) -> dict[str, Any]:
        return self._db.get_user_facts(Session().user)
