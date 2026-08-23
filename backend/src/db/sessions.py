from __future__ import annotations

from datetime import datetime, timedelta
from http import HTTPStatus

from peewee import fn

from tracking.errors import TrackingServiceError

from .models import ChatSession, Message, Project, Tracking

# Same default as chat.session_manager.DEFAULT_OPEN_WINDOW_MINUTES, kept
# here too (same reasoning as that module's own docstring) so this layer
# doesn't have to import the chat layer just for one constant.
_DEFAULT_OPEN_WINDOW_MINUTES = 60.0


class SessionMixin:

    def count_chat_sessions(self, username: str, type: str) -> int:
        return ChatSession.select().where((ChatSession.username == username) & (ChatSession.type == type)).count()

    def create_chat_session(
        self, username: str, project_name: str, revision: int, *,
        datetime_start: datetime | None = None, datetime_end: datetime | None = None,
        start_state: str | None = None, end_state: str | None = None,
        type: str = 'live', title: str | None = None,
    ) -> int:
        """`revision` arrives already resolved by the caller (see
        chat.session_type_strategy.SessionTypeStrategy.revision_for) —
        published for a 'live' session, draft for a 'test' one."""
        if Project.get_or_none(Project.name == project_name) is None:
            raise ValueError(f"Project '{project_name}' does not exist.")
        if title is None:
            title = f"{type.capitalize()} session {self.count_chat_sessions(username, type) + 1}"
        session = ChatSession.create(
            username=username, project_name=project_name, type=type, title=title,
            project_revision=revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state,
        )
        return session.id

    def next_test_user_username(self, project_name: str) -> str:
        n = 1
        while ChatSession.select().where(
            (ChatSession.project_name == project_name) & (ChatSession.username == f'Test user {n}')
        ).exists():
            n += 1
        return f'Test user {n}'

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

    @staticmethod
    def _filter_by_username(query, username: str | None):
        if username is None:
            return query
        return query.where(ChatSession.username == username)

    def get_latest_chat_session(
        self, username: str | None, project_name: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> dict | None:
        query = ChatSession.select().where(ChatSession.project_name == project_name)
        query = self._filter_by_username(query, username)
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        session = query.order_by(ChatSession.datetime_start.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(
        self, username: str | None, project_name: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> list[dict]:
        query = ChatSession.select().where(ChatSession.project_name == project_name)
        query = self._filter_by_username(query, username)
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

    def get_session_labeling_revision(self, session_id: int) -> int:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return session.labeling_revision if session is not None else 0

    def bump_session_labeling_revision(self, session_id: int) -> None:
        ChatSession.update(labeling_revision=ChatSession.labeling_revision + 1).where(ChatSession.id == session_id).execute()

    def touch_chat_session(self, session_id: int, datetime_end: datetime, end_state: str) -> None:
        ChatSession.update(datetime_end=datetime_end, end_state=end_state).where(ChatSession.id == session_id).execute()

    def delete_chat_session(self, session_id: int) -> None:
        Tracking.delete().where(Tracking.session == session_id).execute()
        Message.delete().where(Message.session == session_id).execute()
        ChatSession.delete().where(ChatSession.id == session_id).execute()

    def reassign_sessions_to_test_user(self, session_ids: list[int], test_user_seq: int) -> None:
        sessions = list(ChatSession.select().where(ChatSession.id.in_(session_ids)))
        for session in sessions:
            if not session.username.startswith('Test user '):
                raise TrackingServiceError(
                    f"Session {session.id} belongs to a real user and can't be moved to a test user.",
                    status_code=HTTPStatus.CONFLICT,
                )
        ChatSession.update(username=f'Test user {test_user_seq}').where(ChatSession.id.in_(session_ids)).execute()

    def delete_sessions_by_username(self, username: str) -> None:
        ChatSession.delete().where(ChatSession.username == username).execute()

    def truncate_session(self, session_id: int, cutoff: datetime) -> None:
        Tracking.delete().where((Tracking.session == session_id) & (Tracking.timestamp >= cutoff) & (Tracking.old_state.is_null(True) | (Tracking.old_state != ''))).execute()
        Message.delete().where((Message.session == session_id) & (Message.timestamp >= cutoff)).execute()

    def latest_message_or_signal_timestamp(self, session_id: int) -> datetime | None:
        latest_message = Message.select(fn.MAX(Message.timestamp)).where(Message.session == session_id).scalar()
        latest_signal = Tracking.select(fn.MAX(Tracking.timestamp)).where(Tracking.session == session_id).scalar()
        candidates = [t for t in (latest_message, latest_signal) if t is not None]
        return max(candidates) if candidates else None
