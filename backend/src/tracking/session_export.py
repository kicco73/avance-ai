"""Exports a project's sessions as one JSON array (inverse of
session_import.py's import_session_json). Each message carries its
linked Tracking row inlined; standalone Tracking rows with no
message_id are dropped — the frontend synthesizes the opening transition."""
from __future__ import annotations

import json

from auth.roles import role_satisfies
from db import Db
from db.utils import _utc_iso
from session import Session


class SessionExportManager:
    def __init__(self, db: Db) -> None:
        self._db = db

    def export_sessions(
        self, username: str | None, project_name: str, type: str | tuple[str, ...] = ('live', 'imported'),
    ) -> list[dict]:
        """`type` defaults to every real session. export_project_zip
        narrows this to 'imported' only: a live session only means
        something against the exact database it ran against, so re-importing it elsewhere would misrepresent it as a real conversation."""
        sessions = self._db.list_chat_sessions(None, project_name, type=type)
        if username is not None:
            sessions = [s for s in sessions if self._owns_session(username, s['username'])]
        return [self._export_session(session) for session in sessions]

    @staticmethod
    def _owns_session(username: str, session_username: str) -> bool:
        if session_username == username:
            return True
        return session_username.startswith('Test user ') and role_satisfies(Session().role, 'supervisor')

    def _export_session(self, session: dict) -> dict:
        session_id = session['id']
        tracking_by_message = {
            row['message_id']: row
            for row in self._db.get_signals(session_id)
            if row['message_id'] is not None
        }
        return {
            'name': session['title'],
            'username': session['username'],
            'type': session['type'],
            'timestamp': _utc_iso(session['datetime_start']),
            'datetime_end': _utc_iso(session['datetime_end']),
            'start_state': session['start_state'],
            'end_state': session['end_state'],
            'labeled': session['labeled'],
            'comment': session['comment'],
            'messages': [
                self._export_message(message, tracking_by_message.get(message['id']))
                for message in self._db.get_messages(session_id)
            ],
        }

    @staticmethod
    def _export_message(message: dict, tracking: dict | None) -> dict:
        entry = {
            'role': message['role'],
            'text': message['content'],
            'timestamp': message['timestamp'],
            'audio_text': message['audio_text'],
        }
        if message['tokens'] is not None:
            entry['tokens'] = message['tokens']
        if tracking is None:
            return entry
        entry.update({
            'values': json.loads(tracking['values']) if tracking['values'] else None,
            'expected_state': tracking['expected_state'],
            'expected_values': json.loads(tracking['expected_values']) if tracking['expected_values'] else None,
            'comment': tracking['comment'],
            'old_state': tracking['old_state'],
            'action': tracking['action'],
            'new_state': tracking['new_state'],
        })
        return entry
