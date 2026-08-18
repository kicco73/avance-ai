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
        source: str = 'native', title: str | None = None, allow_draft: bool = False,
    ) -> int:
        # project_revision is stamped once and never touched again (see
        # ChatSession.project_revision's own docstring) — from whatever's
        # published right now, same as always, unless `allow_draft` (only
        # ever passed by EditProjectView.vue's own embedded "Test" chat —
        # see ChatService.get_or_create_current_session/create_session's
        # own allow_draft): then it's stamped with the project's own
        # current *draft* revision instead, published or not, since
        # testing means testing exactly what's actually being edited right
        # now, and is the one place a session is even allowed to exist
        # against a revision nobody's published yet.
        project = Project.get_or_none(Project.name == project_name)
        if project is None:
            raise ValueError(f"Project '{project_name}' does not exist.")
        if allow_draft:
            project_revision = self._current_revision(project_name)
        elif project.published_revision is not None:
            project_revision = project.published_revision
        else:
            raise ValueError(f"Project '{project_name}' has never been published — cannot create a chat session.")
        session = ChatSession.create(
            username=username, project_name=project_name, source=source, title=title,
            project_revision=project_revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state,
        )
        return session.id

    @staticmethod
    def _chat_session_to_dict(session: ChatSession) -> dict:
        return {'id': session.id, 'username': session.username, 'project_name': session.project_name_id, 'source': session.source, 'title': session.title, 'datetime_start': session.datetime_start, 'datetime_end': session.datetime_end, 'start_state': session.start_state, 'end_state': session.end_state}

    def get_chat_session(self, session_id: int) -> dict | None:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return self._chat_session_to_dict(session) if session is not None else None

    def get_latest_chat_session(self, username: str, project_name: str, until: datetime | None=None, source: str | None='native') -> dict | None:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        if source is not None:
            query = query.where(ChatSession.source == source)
        session = query.order_by(ChatSession.datetime_start.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(self, username: str, project_name: str, until: datetime | None=None, source: str | None='native') -> list[dict]:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        if source is not None:
            query = query.where(ChatSession.source == source)
        sessions = query.order_by(ChatSession.datetime_start.desc())
        return [self._chat_session_to_dict(s) for s in sessions]

    def has_open_sessions_for_revision(self, project_name: str, revision: int) -> bool:
        """Whether any *native* session is still open (same "touched
        recently enough" window as ChatSessionManager.is_open, not this
        one user's own single active session — a cross-user existence
        check) at exactly `revision` — ProjectService.preview_publish's
        own gate for whether publishing a new draft should warn the
        caller first (see EditProjectView.vue's own handlePublish):
        freezing the currently *published* revision matters only if some
        live conversation is still actually running on it. Imported
        sessions are excluded (see ChatSession.source) — they were never
        a live conversation to begin with."""
        cutoff = datetime.utcnow() - timedelta(minutes=_DEFAULT_OPEN_WINDOW_MINUTES)
        return ChatSession.select().where(
            (ChatSession.project_name == project_name)
            & (ChatSession.project_revision == revision)
            & (ChatSession.source == 'native')
            & (ChatSession.datetime_end.is_null(False))
            & (ChatSession.datetime_end >= cutoff)
        ).exists()

    def session_has_annotations(self, session_id: int) -> bool:
        return Tracking.select().where((Tracking.session == session_id) & (Tracking.expected_state.is_null(False) | Tracking.expected_values.is_null(False))).exists()

    def get_annotated_session_ids(self, username: str, project_name: str) -> set[int]:
        rows = Tracking.select(Tracking.session).join(ChatSession, on=Tracking.session == ChatSession.id).where((ChatSession.username == username) & (ChatSession.project_name == project_name) & (Tracking.expected_state.is_null(False) | Tracking.expected_values.is_null(False))).distinct()
        return {row.session_id for row in rows}

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
