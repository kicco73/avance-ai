"""Exports a project's sessions as one JSON array (inverse of
session_import.py's import_session_json). Each message carries its
linked Tracking row inlined; a manual action or choice becomes an
`action` entry at its own position among the messages; every other
Tracking row with no message_id is dropped — the frontend synthesizes
the opening transition."""
from __future__ import annotations

import json

from auth.roles import role_satisfies
from db import Db
from db.utils import _utc_iso
from system.web_session import WebSession


class SessionExportManager:
    def __init__(self, db: Db) -> None:
        self._db = db

    def export_sessions(
        self, username: str | None, project_id: str, type: str | tuple[str, ...] = ('live', 'imported'),
    ) -> list[dict]:
        """`type` defaults to every real session. export_project_zip
        narrows this to 'imported' only: a live session only means
        something against the exact database it ran against, so re-importing it elsewhere would misrepresent it as a real conversation."""
        sessions = self._db.list_chat_sessions(None, project_id, type=type)
        if username is not None:
            sessions = [s for s in sessions if self._owns_session(username, s['username'])]
        return [self._export_session(session) for session in sessions]

    @staticmethod
    def _owns_session(username: str, session_username: str) -> bool:
        if session_username == username:
            return True
        return session_username.startswith('Test user ') and role_satisfies(WebSession().role, 'supervisor')

    def _export_session(self, session: dict) -> dict:
        session_id = session['id']
        rows = self._db.get_signals(session_id)
        tracking_by_message = {row['message_id']: row for row in rows if row['message_id'] is not None}
        action_entries = sorted((row for row in rows if row['position'] is not None), key=lambda row: (row['position'], row['id']))
        tool_calls_by_message = self._db.get_tool_calls_by_message(session_id)
        messages = [
            self._export_message(
                message, tracking_by_message.get(message['id']), tool_calls_by_message.get(message['id']),
            )
            for message in self._db.get_messages(session_id)
        ]
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
            'closed_at': _utc_iso(session['closed_at']),
            'close_reason': session['close_reason'],
            'messages': self._interleaved(messages, action_entries),
        }

    @classmethod
    def _interleaved(cls, messages: list[dict], action_entries: list[dict]) -> list[dict]:
        entries: list[dict] = []
        pending = list(action_entries)
        for index, message in enumerate(messages):
            while pending and pending[0]['position'] <= index:
                entries.append(cls._export_action(pending.pop(0)))
            entries.append(message)
        return entries + [cls._export_action(row) for row in pending]

    @staticmethod
    def _export_action(row: dict) -> dict:
        named = {'choice': row['choice']} if row['choice'] is not None else {'action': row['action']}
        return {
            'role': 'action',
            **named,
            'timestamp': row['timestamp'],
            'old_state': row['old_state'],
            'new_state': row['new_state'],
            'values': json.loads(row['values']) if row['values'] else None,
            'origin': row['origin'],
            'expected_state': row['expected_state'],
            'comment': row['comment'],
        }

    @staticmethod
    def _export_message(message: dict, tracking: dict | None, tool_calls: list[dict] | None) -> dict:
        entry = {
            'role': message['role'],
            'text': message['content'],
            'timestamp': message['timestamp'],
            'audio_text': message['audio_text'],
        }
        if message['tokens'] is not None:
            entry['tokens'] = message['tokens']
        if tool_calls is not None:
            entry['tool_calls'] = tool_calls
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
            'origin': tracking['origin'],
        })
        return entry
