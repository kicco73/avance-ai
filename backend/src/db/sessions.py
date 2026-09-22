from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from peewee import fn

from turn.channels import CHANNELS
from system.logging_factory import LoggerFactory
from tracking.errors import TrackingServiceError

from .instrumentation import instrument_queries, write
from .models import SESSION_CLOSE_REASONS, CoreSession, Message, Project, Tracking, User

logger = LoggerFactory.get_logger(__name__)


@instrument_queries
class SessionMixin:

    def chat_session_exists(self, username: str, project_id: str, datetime_start: datetime, datetime_end: datetime) -> bool:
        return CoreSession.select().where(
            (CoreSession.username == username) & (CoreSession.project == project_id)
            & (CoreSession.datetime_start == datetime_start) & (CoreSession.datetime_end == datetime_end)
        ).exists()

    def count_chat_sessions(self, username: str, type: str) -> int:
        return CoreSession.select().where((CoreSession.username == username) & (CoreSession.type == type)).count()

    @write
    def create_chat_session(
        self, username: str, project_id: str, revision: int, *,
        datetime_start: datetime | None = None, datetime_end: datetime | None = None,
        start_state: str | None = None, end_state: str | None = None,
        type: str = 'live', title: str | None = None, channel: str | None = None,
        closed_at: datetime | None = None, close_reason: str | None = None,
    ) -> int:
        """`revision` arrives already resolved by the caller (see
        turn.sessions.session_type_strategy.SessionTypeStrategy.revision_for) —
        published for a 'live' session, draft for a 'test' one."""
        if channel is not None and channel not in CHANNELS:
            raise ValueError(f"Unknown channel '{channel}' — expected one of {CHANNELS}.")
        if Project.get_or_none(Project.id == project_id) is None:
            raise ValueError(f"Project '{project_id}' does not exist.")
        if title is None:
            title = f"{type.capitalize()} session {self.count_chat_sessions(username, type) + 1}"
        user = User.get_or_none(User.id == username)
        session = CoreSession.create(
            username=username, user=user, project=project_id, type=type, title=title,
            project_revision=revision,
            datetime_start=datetime_start, datetime_end=datetime_end,
            start_state=start_state, end_state=end_state, channel=channel,
            closed_at=closed_at, close_reason=close_reason,
        )
        return session.id

    def next_test_user_username(self, project_id: str) -> str:
        n = 1
        while CoreSession.select().where(
            (CoreSession.project == project_id) & (CoreSession.username == f'Test user {n}')
        ).exists():
            n += 1
        return f'Test user {n}'

    @staticmethod
    def _chat_session_to_dict(session: CoreSession) -> dict:
        return {'id': session.id, 'username': session.username, 'project_id': session.project_id, 'type': session.type, 'title': session.title, 'datetime_start': session.datetime_start, 'datetime_end': session.datetime_end, 'start_state': session.start_state, 'end_state': session.end_state, 'project_revision': session.project_revision, 'labeled': session.labeled, 'comment': session.comment, 'channel': session.channel, 'closed_at': session.closed_at, 'close_reason': session.close_reason, 'ai_summary': session.ai_summary}

    def get_chat_session(self, session_id: int) -> dict | None:
        session = CoreSession.get_or_none(CoreSession.id == session_id)
        return self._chat_session_to_dict(session) if session is not None else None

    @staticmethod
    def _filter_by_type(query, type: str | tuple[str, ...] | None):
        """`type`: a single value (the common case), a tuple (e.g.
        ('live', 'imported')), or None for no filter — None only
        exists for get_chat_session's single-row lookup."""
        if type is None:
            return query
        if isinstance(type, tuple):
            return query.where(CoreSession.type.in_(type))
        return query.where(CoreSession.type == type)

    @staticmethod
    def _filter_by_username(query, username: str | None):
        if username is None:
            return query
        return query.where(CoreSession.username == username)

    def get_latest_chat_session(
        self, username: str | None, project_id: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> dict | None:
        query = CoreSession.select().where(CoreSession.project == project_id)
        query = self._filter_by_username(query, username)
        if until is not None:
            query = query.where(CoreSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        session = query.order_by(CoreSession.datetime_start.desc(), CoreSession.id.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(
        self, username: str | None, project_id: str, until: datetime | None=None,
        type: str | tuple[str, ...] | None='live',
    ) -> list[dict]:
        query = CoreSession.select().where(CoreSession.project == project_id)
        query = self._filter_by_username(query, username)
        if until is not None:
            query = query.where(CoreSession.datetime_start <= until)
        query = self._filter_by_type(query, type)
        sessions = query.order_by(CoreSession.datetime_start.desc())
        return [self._chat_session_to_dict(s) for s in sessions]

    def get_first_imported_session(self, project_id: str) -> dict | None:
        session = CoreSession.select().where(
            (CoreSession.project == project_id) & (CoreSession.type == 'imported')
        ).order_by(CoreSession.id.asc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_live_sessions_for_revision(self, project_id: str, revision: int) -> list[dict]:
        sessions = CoreSession.select().where(
            (CoreSession.project == project_id)
            & (CoreSession.project_revision == revision)
            & (CoreSession.type == 'live')
        )
        return [self._chat_session_to_dict(s) for s in sessions]

    @write
    def set_session_title(self, session_id: int, title: str | None) -> None:
        """A domain expert's rename for a session — the same field an
        imported session gets seeded from its uploaded filename, just
        editable after the fact for any session."""
        CoreSession.update(title=title).where(CoreSession.id == session_id).execute()

    @write
    def set_session_comment(self, session_id: int, comment: str | None) -> None:
        """A domain expert's own free-text note on the session as a whole
        (see the "Label sessions" view's own Info tab) — distinct from
        Db.set_signal_comment (Tracking.comment), which is per-message."""
        CoreSession.update(comment=comment).where(CoreSession.id == session_id).execute()

    @write
    def set_session_labeled(self, session_id: int, labeled: bool) -> None:
        """The "Label sessions" view's "Mark done" button — a domain
        expert's explicit, persisted verdict on whether this session's
        been reviewed."""
        CoreSession.update(labeled=labeled).where(CoreSession.id == session_id).execute()

    def get_session_labeling_revision(self, session_id: int) -> int:
        session = CoreSession.get_or_none(CoreSession.id == session_id)
        return session.labeling_revision if session is not None else 0

    @write
    def bump_session_labeling_revision(self, session_id: int) -> None:
        CoreSession.update(labeling_revision=CoreSession.labeling_revision + 1).where(CoreSession.id == session_id).execute()

    @write
    def touch_chat_session(self, session_id: int, datetime_end: datetime, end_state: str | None) -> None:
        updated = CoreSession.update(datetime_end=datetime_end, end_state=end_state).where(
            (CoreSession.id == session_id) & CoreSession.closed_at.is_null()
        ).execute()
        if updated == 0:
            logger.warning("touch_chat_session(): no open session to touch for session_id=%s.", session_id)

    @write
    def close_chat_session(self, session_id: int, closed_at: datetime, reason: str) -> bool:
        if reason not in SESSION_CLOSE_REASONS:
            raise ValueError(f"Unknown close_reason '{reason}' — expected one of {SESSION_CLOSE_REASONS}.")
        updated = CoreSession.update(closed_at=closed_at, close_reason=reason).where(
            (CoreSession.id == session_id) & CoreSession.closed_at.is_null()
        ).execute()
        return updated > 0

    @write
    def set_session_summary(self, session_id: int, summary: str, title: str | None = None) -> None:
        fields = {"ai_summary": summary}
        if title:
            fields["title"] = title
        CoreSession.update(**fields).where(CoreSession.id == session_id).execute()

    def get_recent_session_summaries(self, username: str, project_id: str, limit: int = 3) -> list[str]:
        return [
            row.ai_summary for row in CoreSession.select(CoreSession.ai_summary).where(
                (CoreSession.username == username) & (CoreSession.project == project_id)
                & (CoreSession.ai_summary.is_null(False))
            ).order_by(CoreSession.closed_at.desc()).limit(limit)
        ]

    def list_session_summaries_for_user_project(self, username: str, project_id: str) -> list[dict]:
        return [
            {"id": row.id, "title": row.title, "ai_summary": row.ai_summary, "closed_at": row.closed_at}
            for row in CoreSession.select(
                CoreSession.id, CoreSession.title, CoreSession.ai_summary, CoreSession.closed_at
            ).where(
                (CoreSession.username == username) & (CoreSession.project == project_id)
                & (CoreSession.ai_summary.is_null(False))
            ).order_by(CoreSession.closed_at.desc())
        ]

    @write
    def delete_chat_session(self, session_id: int) -> None:
        CoreSession.delete().where(CoreSession.id == session_id).execute()

    @write
    def reassign_sessions_to_username(self, session_ids: list[int], username: str) -> None:
        """The "Label sessions" view's drag-and-drop between branches —
        moves each of `session_ids` under `username` instead, whether
        that's a "Test user N" branch or any other imported username.
        Imported only: a live session's username is its owner's real,
        authenticated identity, never just a display label to relabel freely."""
        sessions = list(CoreSession.select().where(CoreSession.id.in_(session_ids)))
        for session in sessions:
            if session.type != 'imported':
                raise TrackingServiceError(
                    f"Session {session.id} is a live session and can't be reassigned.",
                    status_code=HTTPStatus.CONFLICT,
                )
        CoreSession.update(username=username).where(CoreSession.id.in_(session_ids)).execute()

    @write
    def delete_sessions_by_username_and_type(self, username: str, type: str) -> list[int]:
        session_ids = [
            row.id for row in CoreSession.select(CoreSession.id).where(
                (CoreSession.username == username) & (CoreSession.type == type)
            )
        ]
        if not session_ids:
            return []
        CoreSession.delete().where(CoreSession.id.in_(session_ids)).execute()
        return session_ids

    @write
    def delete_sessions_by_username_and_project(self, username: str, project_id: str) -> None:
        """The "Label sessions" view's per-branch × button, for any
        non-live branch (a Test user or an arbitrary imported username) —
        scoped to this project only."""
        CoreSession.delete().where(
            (CoreSession.project == project_id) & (CoreSession.username == username)
        ).execute()

    @write
    def delete_imported_sessions(self, project_id: str) -> None:
        """The "Label sessions" view's "Delete all imported sessions"
        button — every imported session of the project, across every
        user."""
        CoreSession.delete().where(
            (CoreSession.project == project_id) & (CoreSession.type == 'imported')
        ).execute()

    @write
    def truncate_session(self, session_id: int, cutoff: datetime) -> None:
        Tracking.delete().where((Tracking.session == session_id) & (Tracking.timestamp >= cutoff) & (Tracking.old_state.is_null(True) | (Tracking.old_state != ''))).execute()
        Message.delete().where((Message.session == session_id) & (Message.timestamp >= cutoff)).execute()

    def latest_message_or_signal_timestamp(self, session_id: int) -> datetime | None:
        latest_message = Message.select(fn.MAX(Message.timestamp)).where(Message.session == session_id).scalar()
        latest_signal = Tracking.select(fn.MAX(Tracking.timestamp)).where(Tracking.session == session_id).scalar()
        candidates = [t for t in (latest_message, latest_signal) if t is not None]
        return max(candidates) if candidates else None
