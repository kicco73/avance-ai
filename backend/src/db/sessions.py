from __future__ import annotations

from datetime import datetime, timedelta

from peewee import fn

from .models import ChatSession, Message, Project, Tracking
from .utils import _utc_iso

# Same default as chat.session_manager.DEFAULT_OPEN_WINDOW_MINUTES, kept
# here too (same reasoning as that module's own docstring) so this layer
# doesn't have to import the chat layer just for one constant.
_DEFAULT_OPEN_WINDOW_MINUTES = 60.0


class SessionMixin:

    def create_chat_session(
        self, username: str, project_name: str,
        datetime_start: datetime | None = None, datetime_end: datetime | None = None,
        start_state: str | None = None, end_state: str | None = None,
        type: str = 'live', title: str | None = None,
    ) -> int:
        """A real session — always stamped with whatever's published
        right now, never a revision nobody's published yet. The embedded
        "Test" chat calls create_draft_chat_session instead."""
        project = Project.get_or_none(Project.name == project_name)
        if project is None:
            raise ValueError(f"Project '{project_name}' does not exist.")
        if project.published_revision is None:
            raise ValueError(f"Project '{project_name}' has never been published — cannot create a chat session.")
        return self._create_chat_session_row(
            username, project_name, project.published_revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state, type=type, title=title,
        )

    def create_draft_chat_session(
        self, username: str, project_name: str,
        datetime_start: datetime | None = None, datetime_end: datetime | None = None,
        start_state: str | None = None, end_state: str | None = None,
        type: str = 'test', title: str | None = None,
    ) -> int:
        """Like create_chat_session, but always stamped with the
        project's current *draft* revision — published or not.
        `type='test'` by default so it's never mistaken for a real session."""
        project = Project.get_or_none(Project.name == project_name)
        if project is None:
            raise ValueError(f"Project '{project_name}' does not exist.")
        return self._create_chat_session_row(
            username, project_name, self._current_revision(project_name),
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state, type=type, title=title,
        )

    def _create_chat_session_row(
        self, username: str, project_name: str, project_revision: int, *,
        datetime_start: datetime | None, datetime_end: datetime | None,
        start_state: str | None, end_state: str | None, type: str, title: str | None,
    ) -> int:
        session = ChatSession.create(
            username=username, project_name=project_name, type=type, title=title,
            project_revision=project_revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state,
        )
        return session.id

    @staticmethod
    def _chat_session_to_dict(session: ChatSession) -> dict:
        return {'id': session.id, 'username': session.username, 'project_name': session.project_name_id, 'type': session.type, 'title': session.title, 'datetime_start': session.datetime_start, 'datetime_end': session.datetime_end, 'start_state': session.start_state, 'end_state': session.end_state, 'project_revision': session.project_revision, 'labeled': session.labeled, 'comment': session.comment}

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

    def get_latest_chat_session(
        self, username: str, project_name: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> dict | None:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        session = query.order_by(ChatSession.datetime_start.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(
        self, username: str, project_name: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> list[dict]:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        sessions = query.order_by(ChatSession.datetime_start.desc())
        return [self._chat_session_to_dict(s) for s in sessions]

    def has_open_sessions_for_revision(self, project_name: str, revision: int) -> bool:
        """Whether any *live* session is still open (a cross-user
        check, not this one user's active session) at exactly
        `revision` — gates whether publishing should warn the caller first."""
        cutoff = datetime.utcnow() - timedelta(minutes=_DEFAULT_OPEN_WINDOW_MINUTES)
        return ChatSession.select().where(
            (ChatSession.project_name == project_name)
            & (ChatSession.project_revision == revision)
            & (ChatSession.type == 'live')
            & (ChatSession.datetime_end.is_null(False))
            & (ChatSession.datetime_end >= cutoff)
        ).exists()

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

    def touch_chat_session(self, session_id: int, datetime_end: datetime, end_state: str) -> None:
        ChatSession.update(datetime_end=datetime_end, end_state=end_state).where(ChatSession.id == session_id).execute()

    def delete_chat_session(self, session_id: int) -> None:
        Tracking.delete().where(Tracking.session == session_id).execute()
        Message.delete().where(Message.session == session_id).execute()
        ChatSession.delete().where(ChatSession.id == session_id).execute()

    def truncate_session(self, session_id: int, cutoff: datetime) -> None:
        Tracking.delete().where((Tracking.session == session_id) & (Tracking.timestamp >= cutoff) & (Tracking.old_state.is_null(True) | (Tracking.old_state != ''))).execute()
        Message.delete().where((Message.session == session_id) & (Message.timestamp >= cutoff)).execute()

    def latest_message_or_signal_timestamp(self, session_id: int) -> datetime | None:
        latest_message = Message.select(fn.MAX(Message.timestamp)).where(Message.session == session_id).scalar()
        latest_signal = Tracking.select(fn.MAX(Tracking.timestamp)).where(Tracking.session == session_id).scalar()
        candidates = [t for t in (latest_message, latest_signal) if t is not None]
        return max(candidates) if candidates else None
