from __future__ import annotations

from chat.sessions.session_ownership import SessionOwnership
from db import Db, _utc_iso
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService


class SessionInsights:
    def __init__(
        self, db: Db, metric_service: MetricService, tracking_service: TrackingService, ownership: SessionOwnership,
    ) -> None:
        self._db = db
        self._metric_service = metric_service
        self._tracking_service = tracking_service
        self._ownership = ownership

    def get_session_signals(self, session_id: int) -> list[dict]:
        self._ownership.require_own_session(session_id)
        return self._tracking_service.get_session_signals(session_id)

    def get_metrics(
        self, project_id: str, message_id: int | None = None, full: bool = False, username: str | None = None,
    ) -> list[dict]:
        until = self._ownership.until_from_message(message_id)
        return self._metric_service.calculate_all(
            until=until, project_id=project_id, include_all_scopes=full, username=username,
        )

    def _session_start_marker(self, session: dict) -> str | None:
        if session['datetime_start'] is not None:
            return _utc_iso(session['datetime_start'])
        messages = self._db.get_messages(session['id'])
        return messages[0]['timestamp'] if messages else None

    def get_metrics_history(self, project_id: str, username: str) -> dict:
        sessions = sorted(
            (
                session for session in self._db.list_chat_sessions(username, project_id, type=None)
                if (session['datetime_end'] or session['datetime_start']) is not None
            ),
            key=lambda session: session['datetime_end'] or session['datetime_start'],
        )
        history = []
        session_starts = []
        for session in sessions:
            until = session['datetime_end'] or session['datetime_start']
            results = self._metric_service.calculate_all(
                until=until, project_id=project_id, include_all_scopes=True, username=username,
            )
            values = {result['name']: result['value'] for result in results if result['value'] is not None}
            if values:
                history.append({'timestamp': _utc_iso(until), 'values': values})
            marker = self._session_start_marker(session)
            if marker is not None:
                session_starts.append({'timestamp': marker, 'title': session['title'], 'end_timestamp': _utc_iso(until)})
        return {'metrics': history, 'session_starts': session_starts}

    def get_latest_signal_values(self, project_id: str, username: str) -> dict:
        sessions = self._db.list_chat_sessions(username, project_id, type='live')
        if not sessions:
            return {'last_session': None, 'session_id': None, 'values': None}
        last_session = sessions[0]
        last_session_payload = {
            'id': last_session['id'], 'start_state': last_session['start_state'], 'end_state': last_session['end_state'],
        }
        for session in sessions:
            for row in reversed(self._db.get_signals(session['id'])):
                if row['values'] is not None:
                    return {'last_session': last_session_payload, 'session_id': session['id'], 'values': row['values']}
        return {'last_session': last_session_payload, 'session_id': last_session['id'], 'values': None}

    def get_timeline(self, project_id: str, username: str) -> dict:
        data = self._db.get_timeline(project_id, username)
        transitions = list(data['transitions'])
        init_transition = self._db.get_project_init_transition(project_id)
        if init_transition is not None and init_transition not in transitions:
            transitions.append(init_transition)
        transitions.sort(key=lambda transition: transition['timestamp'])
        return {'signals': data['signals'], 'transitions': transitions}

    def get_benchmark_metrics(self, project_id: str, session_id: int | None = None) -> list[dict]:
        if session_id is not None:
            self._ownership.require_own_session(session_id)
        return self._metric_service.get_benchmark_metrics(session_id, project_id=project_id)

    def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
        self._ownership.require_own_message(message_id)
        return self._tracking_service.set_message_expected_state(message_id, expected_state)

    def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
        self._ownership.require_own_message(message_id)
        return self._tracking_service.set_message_expected_signals(message_id, expected_values)

    def set_message_comment(self, message_id: int, comment: str | None) -> dict | None:
        self._ownership.require_own_message(message_id)
        return self._tracking_service.set_message_comment(message_id, comment)

    def set_message_reaction(self, message_id: int, reaction: str | None) -> dict | None:
        self._ownership.require_own_message(message_id)
        return self._db.set_message_reaction(message_id, reaction)

    def clear_session_annotations(self, session_id: int) -> None:
        self._ownership.require_own_session(session_id)
        self._tracking_service.clear_session_annotations(session_id)
