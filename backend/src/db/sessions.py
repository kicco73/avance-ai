from __future__ import annotations

from datetime import datetime

from peewee import fn

from .models import ChatSession, Message, Tracking
from .utils import _utc_iso


class SessionMixin:

    def create_chat_session(self, username: str, project_name: str, datetime_start: datetime, datetime_end: datetime, start_state: str, end_state: str) -> int:
        session = ChatSession.create(username=username, project_name=project_name, datetime_start=datetime_start, datetime_end=datetime_end, start_state=start_state, end_state=end_state)
        return session.id

    @staticmethod
    def _chat_session_to_dict(session: ChatSession) -> dict:
        return {'id': session.id, 'username': session.username, 'project_name': session.project_name, 'datetime_start': session.datetime_start, 'datetime_end': session.datetime_end, 'start_state': session.start_state, 'end_state': session.end_state}

    def get_chat_session(self, session_id: int) -> dict | None:
        session = ChatSession.get_or_none(ChatSession.id == session_id)
        return self._chat_session_to_dict(session) if session is not None else None

    def get_latest_chat_session(self, username: str, project_name: str, until: datetime | None=None) -> dict | None:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        session = query.order_by(ChatSession.datetime_start.desc()).first()
        return self._chat_session_to_dict(session) if session is not None else None

    def list_chat_sessions(self, username: str, project_name: str, until: datetime | None=None) -> list[dict]:
        query = ChatSession.select().where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        if until is not None:
            query = query.where(ChatSession.datetime_start <= until)
        sessions = query.order_by(ChatSession.datetime_start.desc())
        return [self._chat_session_to_dict(s) for s in sessions]

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
