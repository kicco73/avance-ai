from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from chat.session_type_strategy import SessionTypeStrategy, get_session_type_strategy
from db import Db
from logging_factory import LoggerFactory
from session import Session

if TYPE_CHECKING:
    from chat.session_report_task import SessionReportScheduler
    from project.project_service import ProjectService

logger = LoggerFactory.get_logger(__name__)

# Default open window, in minutes, when the caller doesn't supply one —
# matches config.yml's chat-service.max-session-duration-in-minutes
# default, kept here too so tests/direct constructions don't need it.
DEFAULT_OPEN_WINDOW_MINUTES = 60.0


class SessionNotWritable(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class ChatSessionManager(object):
    def __init__(self, db: Db, open_window_minutes: float = DEFAULT_OPEN_WINDOW_MINUTES) -> None:
        self._db = db
        self._open_window = timedelta(minutes=open_window_minutes)
        self._session_report_scheduler: "SessionReportScheduler | None" = None

    @property
    def open_window(self) -> timedelta:
        return self._open_window

    def set_session_report_scheduler(self, session_report_scheduler: "SessionReportScheduler") -> None:
        self._session_report_scheduler = session_report_scheduler

    def is_open(self, session: dict, now: datetime | None = None) -> bool:
        """`datetime_end` is just a session's last-activity timestamp;
        every decision about whether a session is open goes through this
        method. A new temporal reader of `datetime_end` must be added to
        the allowlist in tests/test_datetime_end_readers_contract.py."""
        if session["closed_at"] is not None:
            return False
        # A session with no datetime_end yet (an in-progress transcript
        # import) is never "open" either — never a crash from comparing
        # against None.
        if session["datetime_end"] is None:
            return False
        now = now if now is not None else datetime.utcnow()
        strategy = get_session_type_strategy(session["type"])
        return not strategy.is_expired(session, now, self._open_window)

    def has_open_sessions_for_revision(self, project_id: str, revision: int) -> bool:
        sessions = self._db.list_live_sessions_for_revision(project_id, revision)
        return any(self.is_open(session) for session in sessions)

    def get_active_session(self, username: str, project_id: str, type: str = 'live') -> dict | None:
        """The single session `username`+`project_id` may currently
        write to — the most recently started one, if still open. `type`:
        'live'/'test' are separate "active session" pools, never interchangeable."""
        latest = self._db.get_latest_chat_session(username, project_id, type=type)
        if latest is not None and self.is_open(latest):
            return latest
        return None

    def create_session(
        self, strategy: SessionTypeStrategy, project_service: ProjectService, username: str, project_id: str,
    ) -> dict:
        state_key = strategy.starting_state(project_service, project_id, username)
        now = datetime.utcnow()
        revision = strategy.revision_for(project_service, project_id)
        session_id = self._db.create_chat_session(
            username, project_id, revision,
            datetime_start=now, datetime_end=now,
            start_state=state_key, end_state=state_key,
            type=strategy.type_name, channel=Session().channel,
        )
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session

    def get_current_session_if_any_or_create_new(
        self, strategy: SessionTypeStrategy, project_service: ProjectService, username: str, project_id: str,
        session_id: int | None, current_state: str | None = None,
    ) -> dict:
        resolved = strategy.resolve_session(self, username, project_id)
        if resolved is not None:
            if session_id is not None and session_id != resolved["id"]:
                logger.info(
                    "get_current_session_if_any_or_create_new(): caller's session_id=%s is stale for %s/%s, "
                    "current session is %s", session_id, username, project_id, resolved["id"]
                )
            if resolved["channel"] != Session().channel:
                return resolved
            return self._touch(resolved["id"], datetime.utcnow(), current_state)
        return self.create_session(strategy, project_service, username, project_id)

    def acquire_exclusive_session(
        self, strategy: SessionTypeStrategy, project_service: ProjectService, username: str, project_id: str,
        current_state: str | None = None,
    ) -> dict:
        resolved = strategy.resolve_session(self, username, project_id)
        if resolved is None:
            return self.create_session(strategy, project_service, username, project_id)
        if resolved["channel"] == Session().channel:
            return self._touch(resolved["id"], datetime.utcnow(), current_state)
        self.close_session(resolved, "channel-switch")
        return self.create_session(strategy, project_service, username, project_id)

    def require_active_session(
        self, username: str, project_id: str, session_id: int | None, current_state: str
    ) -> dict:
        if session_id is None:
            raise SessionNotWritable("No session specified.", code="session_not_found")
        session = self._db.get_chat_session(session_id)
        if session is None or session["username"] != username or session["project_id"] != project_id:
            raise SessionNotWritable("Session not found.", code="session_not_found")
        if session["closed_at"] is not None:
            raise SessionNotWritable("Session is closed.", code="session_closed")
        strategy = get_session_type_strategy(session["type"])
        active = self.get_active_session(username, project_id, type=session["type"])
        if not strategy.is_valid_write_target(session, active):
            if active is None or active["id"] != session["id"]:
                raise SessionNotWritable("Session is not active.", code="session_superseded")
            raise SessionNotWritable("Session is not active.", code="session_channel_mismatch")
        return self._touch(session["id"], datetime.utcnow(), current_state)

    def touch_session(self, session_id: int, current_state: str | None) -> dict | None:
        return self._touch(session_id, datetime.utcnow(), current_state)

    def close_session(self, session: dict, reason: str, now: datetime | None = None) -> dict:
        if session["closed_at"] is not None:
            return session
        now = now if now is not None else datetime.utcnow()
        self._db.close_chat_session(session["id"], now, reason)
        logger.info(
            "close_session(): session_id=%s username=%s project_id=%s session_channel=%s "
            "current_channel=%s reason=%s",
            session["id"], session["username"], session["project_id"], session["channel"],
            Session().channel, reason,
        )
        result = self._db.get_chat_session(session["id"])
        assert result is not None
        if self._session_report_scheduler is not None:
            self._session_report_scheduler.schedule(result)
        return result

    def _touch(self, session_id: int, now: datetime, current_state: str | None) -> dict:
        self._db.touch_chat_session(session_id, now, current_state)
        session = self._db.get_chat_session(session_id)
        assert session is not None
        return session
