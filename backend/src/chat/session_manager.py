"""Owns the chat-session lifecycle (open/closed, active/inactive,
creation, rotation) on top of db.py's pure ChatSession CRUD — no business
rules live in db.py itself. See db.py's ChatSession for why the model
isn't called `Session` (that name is session.py's unrelated process-local
singleton).

Two distinct, non-interchangeable concepts:
- **open**: a session hasn't expired — less than OPEN_WINDOW has elapsed
  since its datetime_end (see is_open). Purely a per-session, time-based
  fact; says nothing about any other session.
- **active**: within one username+project_name, the single open session
  with the most recent datetime_start (see get_active_session). At most
  one session is ever active at a time — the chat is driven by a single
  per-project state automaton, so letting two sessions both write would
  race on that automaton's state. Every other session for that same
  project is inactive regardless of its own open/closed status: an older
  session can still individually be "open" (not yet expired) while a
  newer one has already superseded it, whether that newer one was
  created automatically (the old one went idle) or manually (see
  create_session, e.g. an explicit "new session" UI action) — "open but
  not active" is a real, reachable state, not just a rounding case.

Two different entry points, deliberately not interchangeable:
- get_or_create_current_session/create_session: "give me *a* session to
  use" — auto-creates if needed, and whatever it returns is by
  construction the active one. Only for the bootstrap endpoint
  (GET /api/chat/session) and the explicit "new session" action
  (POST /api/chat/sessions) — never for an actual chat turn.
- require_active_session: "this exact session must already be the active
  one" — no auto-creation, no silent rotation, and rejects an open-but-
  superseded session just as firmly as a closed one. Used by
  ChatService.process_turn/apply_manual_action: a turn always targets
  the session_id the caller gave, or fails outright (ChatServiceError) if
  it's missing, someone else's, or not the active session. The client
  must explicitly bootstrap or start a new session first — a stale/
  superseded session is never silently replaced out from under a chat
  turn, nor allowed to keep writing once it's been superseded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from db import Db

logger = logging.getLogger(__name__)

# A session is open iff less than this has elapsed since its datetime_end
# was last refreshed (see touch_session/get_or_create_current_session).
OPEN_WINDOW = timedelta(hours=1)


class ChatSessionManager(object):
    def __init__(self, db: Db) -> None:
        self._db = db

    @staticmethod
    def is_open(session: dict, now: datetime | None = None) -> bool:
        now = now if now is not None else datetime.utcnow()
        return now - session["datetime_end"] < OPEN_WINDOW

    def get_active_session(self, username: str, project_name: str) -> dict | None:
        """The single session `username`+`project_name` may currently
        write to — the most recently started one, but only if it's also
        still open; None if there isn't one (nothing yet, or the latest
        has expired too). Every other session for this project, even one
        that's individually still open, is not this one."""
        latest = self._db.get_latest_chat_session(username, project_name)
        if latest is not None and self.is_open(latest):
            return latest
        return None

    def get_or_create_current_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        """The one session `username`+`project_name` may write to right
        now: the most recently started one, if it's still open (touched
        to refresh its datetime_end/end_state), else a freshly created
        one (which immediately becomes "the" active session by having
        the latest datetime_start). `session_id` is the caller's belief
        about which session is current — never trusted for the decision
        (see module docstring), only logged when it's stale so a rotation
        elsewhere is observable."""
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

    def require_active_session(
        self, username: str, project_name: str, session_id: int | None, current_state: str
    ) -> dict:
        """The exact session a chat turn must write to — raises
        ValueError (never falls back to creating or picking a different
        one) if `session_id` is missing, doesn't exist, belongs to
        another user/project, or isn't the currently active session for
        that project (whether it's outright closed, or merely open but
        already superseded by a newer one — both are rejected the same
        way). On success, touches it (same refresh as
        get_or_create_current_session) and returns it."""
        if session_id is None:
            raise ValueError("No session specified.")
        session = self._db.get_chat_session(session_id)
        if session is None or session["username"] != username or session["project_name"] != project_name:
            raise ValueError("Session not found.")
        active = self.get_active_session(username, project_name)
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

    def touch_session(self, session_id: int, current_state: str) -> dict | None:
        return self._touch(session_id, datetime.utcnow(), current_state)

    def _touch(self, session_id: int, now: datetime, current_state: str) -> dict:
        self._db.touch_chat_session(session_id, now, current_state)
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def get_session(self, session_id: int) -> dict | None:
        return self._db.get_chat_session(session_id)
