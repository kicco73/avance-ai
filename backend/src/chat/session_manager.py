from __future__ import annotations

import logging
from datetime import datetime, timedelta

from db import Db

logger = logging.getLogger(__name__)

# Default open window, in minutes, when the caller doesn't supply one —
# matches config.yml's chat-service.max_session_duration_in_minutes
# default, kept here too so tests/direct constructions don't need it.
DEFAULT_OPEN_WINDOW_MINUTES = 60.0


class ChatSessionManager(object):
    def __init__(self, db: Db, open_window_minutes: float = DEFAULT_OPEN_WINDOW_MINUTES) -> None:
        self._db = db
        self._open_window = timedelta(minutes=open_window_minutes)

    @property
    def open_window(self) -> timedelta:
        return self._open_window

    def is_open(self, session: dict, now: datetime | None = None) -> bool:
        # An imported session has no datetime_end at all — never had a
        # live conversation window, so "open" always means False here,
        # never a crash from comparing against None.
        if session["datetime_end"] is None:
            return False
        now = now if now is not None else datetime.utcnow()
        return now - session["datetime_end"] < self._open_window

    def get_active_session(self, username: str, project_name: str, source: str = 'native') -> dict | None:
        """The single session `username`+`project_name` may currently
        write to — the most recently started one, if still open. `source`:
        'native'/'test' are separate "active session" pools, never interchangeable."""
        latest = self._db.get_latest_chat_session(username, project_name, source=source)
        if latest is not None and self.is_open(latest):
            return latest
        return None

    def get_or_create_current_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        """The one session `username`+`project_name` may write to right
        now: the most recently started one if still open, else a freshly
        created one. `session_id` is only logged when stale, never trusted."""
        now = datetime.utcnow()
        active = self.get_active_session(username, project_name)
        if active is not None:
            if session_id is not None and session_id != active["id"]:
                logger.info(
                    "get_or_create_current_session(): caller's session_id=%s is stale for %s/%s, "
                    "current session is %s", session_id, username, project_name, active["id"]
                )
            return self._touch(active["id"], now, current_state)
        return self.create_session(username, project_name, current_state)

    def get_or_create_current_draft_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        """Like get_or_create_current_session, but a fresh session is
        stamped against the project's current *draft* revision instead
        of requiring a published one."""
        now = datetime.utcnow()
        active = self.get_active_session(username, project_name, source='test')
        if active is not None:
            if session_id is not None and session_id != active["id"]:
                logger.info(
                    "get_or_create_current_draft_session(): caller's session_id=%s is stale for %s/%s, "
                    "current session is %s", session_id, username, project_name, active["id"]
                )
            return self._touch(active["id"], now, current_state)
        return self.create_draft_session(username, project_name, current_state)

    def require_active_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        """The exact session a chat turn must write to — raises
        ValueError (never falls back to creating/picking another one) if
        `session_id` is missing, unknown, or not the active session."""
        if session_id is None:
            raise ValueError("No session specified.")
        session = self._db.get_chat_session(session_id)
        if session is None or session["username"] != username or session["project_name"] != project_name:
            raise ValueError("Session not found.")
        active = self.get_active_session(username, project_name, source=session["source"])
        if active is None or active["id"] != session["id"]:
            raise ValueError("Session is not active.")
        return self._touch(session["id"], datetime.utcnow(), current_state)

    def create_session(self, username: str, project_name: str, current_state: str) -> dict:
        now = datetime.utcnow()
        session_id = self._db.create_chat_session(
            username=username,
            project_name=project_name,
            datetime_start=now,
            datetime_end=now,
            start_state=current_state,
            end_state=current_state,
        )
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def create_draft_session(self, username: str, project_name: str, current_state: str) -> dict:
        now = datetime.utcnow()
        session_id = self._db.create_draft_chat_session(
            username=username,
            project_name=project_name,
            datetime_start=now,
            datetime_end=now,
            start_state=current_state,
            end_state=current_state,
        )
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def touch_session(self, session_id: int, current_state: str) -> dict | None:
        return self._touch(session_id, datetime.utcnow(), current_state)

    def _touch(self, session_id: int, now: datetime, current_state: str) -> dict:
        self._db.touch_chat_session(session_id, now, current_state)
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def get_session(self, session_id: int) -> dict | None:
        return self._db.get_chat_session(session_id)
