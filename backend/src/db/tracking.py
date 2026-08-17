from __future__ import annotations

import json
import logging
from datetime import datetime

from .models import ChatSession, Tracking, DEFAULT_USER
from .utils import _utc_iso

logger = logging.getLogger(__name__)


class TrackingMixin:

    def save_signal_snapshot(self, values: dict, session_id: int, message_id: int | None=None) -> int:
        row = Tracking.create(session=session_id, values=json.dumps(values), message=message_id)
        return row.id

    def get_latest_signal_snapshot(self, project_name: str) -> dict | None:
        row = Tracking.select().join(ChatSession, on=Tracking.session == ChatSession.id).where((ChatSession.project_name == project_name) & Tracking.values.is_null(False)).order_by(Tracking.timestamp.desc()).first()
        if row is None:
            return None
        return json.loads(row.values)

    def get_signals(self, session_id: int) -> list[dict]:
        rows = Tracking.select().where((Tracking.session == session_id) & Tracking.env.is_null(True) & Tracking.action_env.is_null(True)).order_by(Tracking.timestamp.asc(), Tracking.id.asc())
        return [{'id': row.id, 'timestamp': _utc_iso(row.timestamp), 'values': row.values, 'expected_values': row.expected_values, 'expected_state': row.expected_state, 'old_state': row.old_state, 'action': row.action, 'new_state': row.new_state, 'message_id': row.message_id} for row in rows]

    def save_transition(self, old_state: str, action: str, new_state: str, session_id: int, transition_log_level: str, signal_values: dict | None=None, message_id: int | None=None) -> int:
        row = Tracking.create(session=session_id, old_state=old_state, action=action, new_state=new_state, values=json.dumps(signal_values) if signal_values is not None else None, message=message_id)
        trigger_type = 'auto' if signal_values is not None else 'manual'
        level = getattr(logging, transition_log_level)
        message = f'State transition: {old_state} -> {new_state} (action={action}, trigger={trigger_type})'
        if signal_values:
            message += f' signals={signal_values}'
        logger.log(level, message)
        return row.id

    def link_signal_to_message(self, signal_row_id: int, message_id: int) -> None:
        Tracking.update(message=message_id).where(Tracking.id == signal_row_id).execute()

    def get_signal_row_by_message(self, message_id: int) -> dict | None:
        row = Tracking.get_or_none(Tracking.message == message_id)
        if row is None:
            return None
        return {'id': row.id, 'timestamp': _utc_iso(row.timestamp), 'values': row.values, 'expected_values': row.expected_values, 'expected_state': row.expected_state, 'old_state': row.old_state, 'action': row.action, 'new_state': row.new_state, 'message_id': row.message_id}

    def set_signal_expected_state(self, signal_row_id: int, expected_state: str | None) -> None:
        Tracking.update(expected_state=expected_state).where(Tracking.id == signal_row_id).execute()

    def set_signal_expected_values(self, signal_row_id: int, expected_values: dict | None) -> None:
        serialized = json.dumps(expected_values) if expected_values else None
        Tracking.update(expected_values=serialized).where(Tracking.id == signal_row_id).execute()

    def delete_signal_row(self, signal_row_id: int) -> None:
        Tracking.delete().where(Tracking.id == signal_row_id).execute()

    def clear_session_annotations(self, session_id: int) -> None:
        Tracking.update(expected_state=None, expected_values=None).where(Tracking.session == session_id).execute()
        Tracking.delete().where((Tracking.session == session_id) & (Tracking.old_state == '')).execute()

    def _latest_transition(self, project_name: str, *, real_only: bool=False, until: datetime | None=None) -> Tracking | None:
        query = Tracking.select().join(ChatSession, on=Tracking.session == ChatSession.id).where((ChatSession.project_name == project_name) & Tracking.new_state.is_null(False))
        if real_only:
            query = query.where(Tracking.old_state != Tracking.new_state)
        if until is not None:
            query = query.where(Tracking.timestamp <= until)
        return query.order_by(Tracking.timestamp.desc()).first()

    def get_current_state(self, project_name: str) -> str | None:
        transition = self._latest_transition(project_name)
        return transition.new_state if transition else None

    def get_last_transition_timestamp(self, project_name: str, until: datetime | None=None) -> datetime | None:
        transition = self._latest_transition(project_name, real_only=True, until=until)
        return transition.timestamp if transition else None

    def get_env(self, project_name: str, user: str=DEFAULT_USER, until: datetime | None=None) -> dict:
        query = Tracking.select(Tracking.env).join(ChatSession, on=Tracking.session == ChatSession.id).where((ChatSession.project_name == project_name) & (ChatSession.username == user) & Tracking.env.is_null(False))
        if until is not None:
            query = query.where(Tracking.timestamp <= until)
        row = query.order_by(Tracking.timestamp.desc()).first()
        return json.loads(row.env) if row is not None else {}

    def set_env(self, project_name: str, env: dict, user: str=DEFAULT_USER, message_id: int | None=None) -> None:
        session = self.get_latest_chat_session(user, project_name)
        if session is None:
            return
        Tracking.create(session=session['id'], env=json.dumps(env), message=message_id)

    def get_action_env(self, project_name: str, user: str=DEFAULT_USER, until: datetime | None=None) -> dict:
        query = Tracking.select(Tracking.action_env).join(ChatSession, on=Tracking.session == ChatSession.id).where((ChatSession.project_name == project_name) & (ChatSession.username == user) & Tracking.action_env.is_null(False))
        if until is not None:
            query = query.where(Tracking.timestamp <= until)
        row = query.order_by(Tracking.timestamp.desc()).first()
        return json.loads(row.action_env) if row is not None else {}

    def set_action_env(self, project_name: str, action_env: dict, user: str=DEFAULT_USER) -> None:
        session = self.get_latest_chat_session(user, project_name)
        if session is None:
            return
        Tracking.create(session=session['id'], action_env=json.dumps(action_env))
