from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from chat.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
from db import Db
from logging_factory import LoggerFactory

if TYPE_CHECKING:
    from project.project_service import ProjectService

logger = LoggerFactory.get_logger(__name__)

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
        # A session with no datetime_end yet (an in-progress transcript
        # import) is never "open" either — never a crash from comparing
        # against None.
        if session["datetime_end"] is None:
            return False
        now = now if now is not None else datetime.utcnow()
        strategy = get_session_type_strategy(session["type"])
        return not strategy.is_expired(session, now, self._open_window)

    def get_active_session(self, username: str, project_name: str, type: str = 'live') -> dict | None:
        """The single session `username`+`project_name` may currently
        write to — the most recently started one, if still open. `type`:
        'live'/'test' are separate "active session" pools, never interchangeable."""
        latest = self._db.get_latest_chat_session(username, project_name, type=type)
        if latest is not None and self.is_open(latest):
            return latest
        return None

    def create_session(
        self, strategy: SessionTypeStrategy, project_service: ProjectService, username: str, project_name: str,
    ) -> dict:
        state_key = strategy.starting_state(project_service, project_name, username)
        now = datetime.utcnow()
        revision = strategy.revision_for(project_service, project_name)
        session_id = self._db.create_chat_session(
            username, project_name, revision,
            datetime_start=now, datetime_end=now,
            start_state=state_key, end_state=state_key,
            type=strategy.type_name,
        )
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def resolve_or_create_session(
        self, strategy: SessionTypeStrategy, project_service: ProjectService, username: str, project_name: str,
        session_id: int | None, current_state: str | None = None,
    ) -> dict:
        resolved = strategy.resolve_session(self, username, project_name)
        if resolved is not None:
            if session_id is not None and session_id != resolved["id"]:
                logger.info(
                    "resolve_or_create_session(): caller's session_id=%s is stale for %s/%s, "
                    "current session is %s", session_id, username, project_name, resolved["id"]
                )
            return self._touch(resolved["id"], datetime.utcnow(), current_state)
        return self.create_session(strategy, project_service, username, project_name)

    def require_active_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        if session_id is None:
            raise ValueError("No session specified.")
        session = self._db.get_chat_session(session_id)
        if session is None or session["username"] != username or session["project_name"] != project_name:
            raise ValueError("Session not found.")
        strategy = get_session_type_strategy(session["type"])
        active = self.get_active_session(username, project_name, type=session["type"])
        if not strategy.is_valid_write_target(session, active):
            raise ValueError("Session is not active.")
        return self._touch(session["id"], datetime.utcnow(), current_state)

    def touch_session(self, session_id: int, current_state: str | None) -> dict | None:
        return self._touch(session_id, datetime.utcnow(), current_state)

    def _touch(self, session_id: int, now: datetime, current_state: str | None) -> dict:
        self._db.touch_chat_session(session_id, now, current_state)
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session
