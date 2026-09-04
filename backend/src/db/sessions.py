from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from peewee import fn

from chat.channels import CHANNELS, NATIVE_CHAT
from logging_factory import LoggerFactory
from tracking.errors import TrackingServiceError

from .models import SESSION_CLOSE_REASONS, ChatSession, Message, Project, Tracking, User

logger = LoggerFactory.get_logger(__name__)


class SessionMixin:

    def chat_session_exists(self, username: str, project_id: str, datetime_start: datetime, datetime_end: datetime) -> bool:
        return ChatSession.select().where(
            (ChatSession.username == username) & (ChatSession.project == project_id)
            & (ChatSession.datetime_start == datetime_start) & (ChatSession.datetime_end == datetime_end)
        ).exists()

    def count_chat_sessions(self, username: str, type: str) -> int:
        return ChatSession.select().where((ChatSession.username == username) & (ChatSession.type == type)).count()

    def create_chat_session(
        self, username: str, project_id: str, revision: int, *,
        datetime_start: datetime | None = None, datetime_end: datetime | None = None,
        start_state: str | None = None, end_state: str | None = None,
        type: str = 'live', title: str | None = None, channel: str = NATIVE_CHAT,
        closed_at: datetime | None = None, close_reason: str | None = None,
    ) -> int:
        """`revision` arrives already resolved by the caller (see
        chat.session_type_strategy.SessionTypeStrategy.revision_for) —
        published for a 'live' session, draft for a 'test' one."""
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel '{channel}' — expected one of {CHANNELS}.")
        if Project.get_or_none(Project.id == project_id) is None:
            raise ValueError(f"Project '{project_id}' does not exist.")
        if title is None:
            title = f"{type.capitalize()} session {self.count_chat_sessions(username, type) + 1}"
        # `username` may be a real registered account's email or an
        # imported transcript's synthetic identity (see
        # next_test_user_username below) — user stays null for the
        # latter, since there's no User row to point at.
        user = User.get_or_none(User.id == username)
        session = ChatSession.create(
            username=username, user=user, project=project_id, type=type, title=title,
            project_revision=revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state, channel=channel,
            closed_at=closed_at, close_reason=close_reason,
        )
        return session.id

    def next_test_user_username(self, project_id: str) -> str:
        n = 1
        while ChatSession.select().where(
            (ChatSession.project == project_id) & (ChatSession.username == f'Test user {n}')
        ).exists():
            n += 1
        return f'Test user {n}'

    @staticmethod
    def _chat_session_to_dict(session: ChatSession) -> dict:
        return {'id': session.id, 'username': session.username, 'project_id': session.project_id, 'type': session.type, 'title': session.title, 'datetime_start': session.datetime_start, 'datetime_end': session.datetime_end, 'start_state': session.start_state, 'end_state': session.end_state, 'project_revision': session.project_revision, 'labeled': session.labeled, 'comment': session.comment, 'channel': session.channel, 'closed_at': session.closed_at, 'close_reason': session.close_reason}

    def get_chat_session(self, session_id: int) -> dict | None:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return self._chat_session_to_dict(session) if session is not None else None

    @staticmethod
    def _filter_by_type(query, type: str | tuple[str, ...] | None):
        """`type`: a single value (the common case), a tuple (e.g.
        ('live', 'imported')), or None for no filter — None only
        exists for get_chat_session's single-row lookup."""
        if type is None:
            return query
        if isinstance(type, tuple):
            return query.where(ChatSession.type.in_(type))
        return query.where(ChatSession.type == type)

    @staticmethod
    def _filter_by_username(query, username: str | None):
        if username is None:
            return query
        return query.where(ChatSession.username == username)

    def get_latest_chat_session(
        self, username: str | None, project_id: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> dict | None:
        query = ChatSession.select().where(ChatSession.project == project_id)
        query = self._filter_by_username(query, username)
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        session = query.order_by(ChatSession.datetime_start.desc(), ChatSession.id.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def get_previous_chat_session(
        self, username: str, project_id: str, before_session_id: int,
        type: str | tuple[str, ...] | None='live',
    ) -> dict | None:
        """The session immediately before `before_session_id` in this
        (username, project_id)'s own history, ordered by id — unlike
        get_latest_chat_session, the answer never changes on a later call
        against the same still-current session. ChatService's own
        legal/terms.md re-notice check relies on exactly that stability."""
        query = ChatSession.select().where(
            (ChatSession.project == project_id) & (ChatSession.username == username)
            & (ChatSession.id < before_session_id)
        )
        query = self._filter_by_type(query, type)
        session = query.order_by(ChatSession.id.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(
        self, username: str | None, project_id: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> list[dict]:
        query = ChatSession.select().where(ChatSession.project == project_id)
        query = self._filter_by_username(query, username)
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        sessions = query.order_by(ChatSession.datetime_start.desc())
        return [self._chat_session_to_dict(s) for s in sessions]

    def get_first_imported_session(self, project_id: str) -> dict | None:
        session = ChatSession.select().where(
            (ChatSession.project == project_id) & (ChatSession.type == 'imported')
        ).order_by(ChatSession.id.asc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_live_sessions_for_revision(self, project_id: str, revision: int) -> list[dict]:
        sessions = ChatSession.select().where(
            (ChatSession.project == project_id)
            & (ChatSession.project_revision == revision)
            & (ChatSession.type == 'live')
        )
        return [self._chat_session_to_dict(s) for s in sessions]

    def set_session_title(self, session_id: int, title: str | None) -> None:
        """A domain expert's rename for a session — the same field an
        imported session gets seeded from its uploaded filename, just
        editable after the fact for any session."""
        ChatSession.update(title=title).where(ChatSession.id == session_id).execute()

    def set_session_comment(self, session_id: int, comment: str | None) -> None:
        """A domain expert's own free-text note on the session as a whole
        (see the "Label sessions" view's own Info tab) — distinct from
        Db.set_signal_comment (Tracking.comment), which is per-message."""
        ChatSession.update(comment=comment).where(ChatSession.id == session_id).execute()

    def set_session_labeled(self, session_id: int, labeled: bool) -> None:
        """The "Label sessions" view's "Mark done" button — a domain
        expert's explicit, persisted verdict on whether this session's
        been reviewed."""
        ChatSession.update(labeled=labeled).where(ChatSession.id == session_id).execute()

    def get_session_labeling_revision(self, session_id: int) -> int:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return session.labeling_revision if session is not None else 0

    def bump_session_labeling_revision(self, session_id: int) -> None:
        ChatSession.update(labeling_revision=ChatSession.labeling_revision + 1).where(ChatSession.id == session_id).execute()

    def touch_chat_session(self, session_id: int, datetime_end: datetime, end_state: str | None) -> None:
        updated = ChatSession.update(datetime_end=datetime_end, end_state=end_state).where(
            (ChatSession.id == session_id) & ChatSession.closed_at.is_null()
        ).execute()
        if updated == 0:
            logger.warning("touch_chat_session(): no open session to touch for session_id=%s.", session_id)

    def close_chat_session(self, session_id: int, closed_at: datetime, reason: str) -> bool:
        if reason not in SESSION_CLOSE_REASONS:
            raise ValueError(f"Unknown close_reason '{reason}' — expected one of {SESSION_CLOSE_REASONS}.")
        updated = ChatSession.update(closed_at=closed_at, close_reason=reason).where(
            (ChatSession.id == session_id) & ChatSession.closed_at.is_null()
        ).execute()
        return updated > 0

    def delete_chat_session(self, session_id: int) -> None:
        Tracking.delete().where(Tracking.session == session_id).execute()
        Message.delete().where(Message.session == session_id).execute()
        ChatSession.delete().where(ChatSession.id == session_id).execute()

    def reassign_sessions_to_username(self, session_ids: list[int], username: str) -> None:
        """The "Label sessions" view's drag-and-drop between branches —
        moves each of `session_ids` under `username` instead, whether
        that's a "Test user N" branch or any other imported username.
        Imported only: a live session's username is its owner's real,
        authenticated identity, never just a display label to relabel freely."""
        sessions = list(ChatSession.select().where(ChatSession.id.in_(session_ids)))
        for session in sessions:
            if session.type != 'imported':
                raise TrackingServiceError(
                    f"Session {session.id} is a live session and can't be reassigned.",
                    status_code=HTTPStatus.CONFLICT,
                )
        ChatSession.update(username=username).where(ChatSession.id.in_(session_ids)).execute()

    def delete_sessions_by_username_and_type(self, username: str, type: str) -> None:
        session_ids = [
            row.id for row in ChatSession.select(ChatSession.id).where(
                (ChatSession.username == username) & (ChatSession.type == type)
            )
        ]
        if not session_ids:
            return
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.id.in_(session_ids)).execute()

    def delete_sessions_by_username_and_project(self, username: str, project_id: str) -> None:
        """The "Label sessions" view's per-branch × button, for any
        non-live branch (a Test user or an arbitrary imported username) —
        scoped to this project only, and cleans up Message/Tracking rows
        too, unlike ChatSession.delete() alone would."""
        session_ids = [
            row.id for row in ChatSession.select(ChatSession.id).where(
                (ChatSession.project == project_id) & (ChatSession.username == username)
            )
        ]
        if not session_ids:
            return
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.id.in_(session_ids)).execute()

    def delete_imported_sessions(self, project_id: str) -> None:
        """The "Label sessions" view's "Delete all imported sessions"
        button — every imported session of the project, across every
        user, cleaned up the same way delete_chat_session cleans up one."""
        session_ids = [
            row.id for row in ChatSession.select(ChatSession.id).where(
                (ChatSession.project == project_id) & (ChatSession.type == 'imported')
            )
        ]
        if not session_ids:
            return
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.id.in_(session_ids)).execute()

    def truncate_session(self, session_id: int, cutoff: datetime) -> None:
        Tracking.delete().where((Tracking.session == session_id) & (Tracking.timestamp >= cutoff) & (Tracking.old_state.is_null(True) | (Tracking.old_state != ''))).execute()
        Message.delete().where((Message.session == session_id) & (Message.timestamp >= cutoff)).execute()

    def latest_message_or_signal_timestamp(self, session_id: int) -> datetime | None:
        latest_message = Message.select(fn.MAX(Message.timestamp)).where(Message.session == session_id).scalar()
        latest_signal = Tracking.select(fn.MAX(Tracking.timestamp)).where(Tracking.session == session_id).scalar()
        candidates = [t for t in (latest_message, latest_signal) if t is not None]
        return max(candidates) if candidates else None
