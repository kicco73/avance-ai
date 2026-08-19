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
        source: str = 'native', title: str | None = None,
    ) -> int:
        """A real session — always stamped with whatever's published right
        now (see ChatSession.project_revision's own docstring), never a
        revision nobody's published yet. Every entry point except
        EditProjectView.vue's own embedded "Test" chat calls this one; that
        one calls create_draft_chat_session instead — the only place a
        session is allowed to exist against an unpublished revision."""
        project = Project.get_or_none(Project.name == project_name)
        if project is None:
            raise ValueError(f"Project '{project_name}' does not exist.")
        if project.published_revision is None:
            raise ValueError(f"Project '{project_name}' has never been published — cannot create a chat session.")
        return self._create_chat_session_row(
            username, project_name, project.published_revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state, source=source, title=title,
        )

    def create_draft_chat_session(
        self, username: str, project_name: str,
        datetime_start: datetime | None = None, datetime_end: datetime | None = None,
        start_state: str | None = None, end_state: str | None = None,
        source: str = 'test', title: str | None = None,
    ) -> int:
        """Like create_chat_session, but always stamped with the project's
        own current *draft* revision instead — published or not, since
        testing (see ChatService.create_draft_session/get_or_create_
        current_draft_session, EditProjectView.vue's own embedded "Test"
        chat, the only caller) means testing exactly what's actually being
        edited right now. `source='test'` (not 'native') by default: a
        draft session must never be indistinguishable from a real one —
        every query elsewhere that resolves/lists "the" active/native
        session (get_latest_chat_session/list_chat_sessions' own source
        default) would otherwise happily pick one of these up instead,
        exactly the isolation break this field exists to prevent."""
        project = Project.get_or_none(Project.name == project_name)
        if project is None:
            raise ValueError(f"Project '{project_name}' does not exist.")
        return self._create_chat_session_row(
            username, project_name, self._current_revision(project_name),
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state, source=source, title=title,
        )

    def _create_chat_session_row(
        self, username: str, project_name: str, project_revision: int, *,
        datetime_start: datetime | None, datetime_end: datetime | None,
        start_state: str | None, end_state: str | None, source: str, title: str | None,
    ) -> int:
        session = ChatSession.create(
            username=username, project_name=project_name, source=source, title=title,
            project_revision=project_revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state,
        )
        return session.id

    @staticmethod
    def _chat_session_to_dict(session: ChatSession) -> dict:
        return {'id': session.id, 'username': session.username, 'project_name': session.project_name_id, 'source': session.source, 'title': session.title, 'datetime_start': session.datetime_start, 'datetime_end': session.datetime_end, 'start_state': session.start_state, 'end_state': session.end_state, 'project_revision': session.project_revision, 'labeled': session.labeled}

    def get_chat_session(self, session_id: int) -> dict | None:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return self._chat_session_to_dict(session) if session is not None else None

    @staticmethod
    def _filter_by_source(query, source: str | tuple[str, ...] | None):
        """`source`: a single value (the common case), a tuple (e.g.
        ('native', 'imported') — every *real* session, excluding test
        ones — see ChatService.list_sessions), or None for no filter at
        all (every caller of this module still passes something real
        though; None only exists for get_chat_session's own single-row
        lookup, which has no source concept to filter by)."""
        if source is None:
            return query
        if isinstance(source, tuple):
            return query.where(ChatSession.source.in_(source))
        return query.where(ChatSession.source == source)

    def get_latest_chat_session(
        self, username: str, project_name: str, until: datetime | None=None,
        source: str | tuple[str, ...] | None='native',
    ) -> dict | None:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_source(query, source)
        session = query.order_by(ChatSession.datetime_start.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(
        self, username: str, project_name: str, until: datetime | None=None,
        source: str | tuple[str, ...] | None='native',
    ) -> list[dict]:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        query = self._filter_by_source(query, source)
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

    def set_session_labeled(self, session_id: int, labeled: bool) -> None:
        """The "Label sessions" view's own "Mark done" button (see
        ChatService.mark_session_labeled) — a domain expert's explicit,
        persisted verdict on whether this session's been reviewed,
        replacing the old any-Tracking-row-has-an-annotation heuristic
        this class used to compute it with on every read (see db.py's
        own one-time _backfill_labeled_from_old_annotation_heuristic,
        which seeded this column from that same heuristic the moment it
        was introduced, never touched again after)."""
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
