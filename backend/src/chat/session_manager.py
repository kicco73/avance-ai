from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from chat.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
from db import Db

if TYPE_CHECKING:
    from project.project_service import ProjectService

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
        automaton: Automaton, current_state: str | None = None,
    ) -> dict:
        state_key = current_state if strategy.type_name == 'live' else strategy.starting_state(automaton)
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
        session_id: int | None, automaton: Automaton, current_state: str | None = None,
    ) -> dict:
        """The one session `username`+`project_name` may write to right
        now. `strategy.resolves_by_id()` (test): `session_id`, when given
        and still valid, is always authoritative — never falls back to
        "most recently started". Otherwise (live): the most recently
        started still-open session, `session_id` only logged when stale, never trusted."""
        now = datetime.utcnow()
        if strategy.resolves_by_id():
            if session_id is not None:
                session = self._db.get_chat_session(session_id)
                if (
                    session is not None and session["username"] == username
                    and session["project_name"] == project_name and session["type"] == strategy.type_name
                ):
                    return self._touch(session["id"], now, current_state)
            return self.create_session(strategy, project_service, username, project_name, automaton, current_state)
        active = self.get_active_session(username, project_name, type=strategy.type_name)
        if active is not None:
            if session_id is not None and session_id != active["id"]:
                logger.info(
                    "resolve_or_create_session(): caller's session_id=%s is stale for %s/%s, "
                    "current session is %s", session_id, username, project_name, active["id"]
                )
            return self._touch(active["id"], now, current_state)
        return self.create_session(strategy, project_service, username, project_name, automaton, current_state)

    def require_active_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        """The exact session a chat turn must write to — raises
        ValueError (never falls back to creating/picking another one) if
        `session_id` is missing, unknown, or not the active session.
        A strategy whose type resolves_by_id() (test) skips the
        active-session uniqueness check entirely — existing and owned is
        already enough, since test sessions aren't a single-active-slot pool."""
        if session_id is None:
            raise ValueError("No session specified.")
        session = self._db.get_chat_session(session_id)
        if session is None or session["username"] != username or session["project_name"] != project_name:
            raise ValueError("Session not found.")
        strategy = get_session_type_strategy(session["type"])
        if not strategy.resolves_by_id():
            active = self.get_active_session(username, project_name, type=session["type"])
            if active is None or active["id"] != session["id"]:
                raise ValueError("Session is not active.")
        return self._touch(session["id"], datetime.utcnow(), current_state)

    def touch_session(self, session_id: int, current_state: str) -> dict | None:
        return self._touch(session_id, datetime.utcnow(), current_state)

    def _touch(self, session_id: int, now: datetime, current_state: str) -> dict:
        self._db.touch_chat_session(session_id, now, current_state)
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def get_session(self, session_id: int) -> dict | None:
        return self._db.get_chat_session(session_id)
