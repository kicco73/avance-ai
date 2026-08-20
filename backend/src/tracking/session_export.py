"""Exporting a project's own sessions (native and imported alike) as one
JSON array — the "Label sessions" view's own "Download all" button. The
inverse of session_import.py's own import_session_json: every message
carries whatever Tracking row is linked to it (see Db.get_signals) inlined
directly on the message itself, rather than as a separate parallel list —
a message and "the transition/annotation attached to it" are one visual
unit in the chat timeline this is meant to reconstruct (see benchmark
Timeline.js's own buildTimeline), so the export mirrors that shape.

Deliberately scoped to what BenchmarkTimeline.js/ChatTimeline.vue actually
need to rebuild the "Label sessions" view exactly as saved: session
metadata (name/timestamps/start-end state/labeled/comment) plus each
message's own role/text/timestamp/audio_text plus whichever Tracking
fields matter for the timeline and its own annotations (values/expected_
state/expected_values/comment/old_state/action/new_state). A standalone
Tracking row with no message_id (e.g. env/action_env bookkeeping, already
excluded by Db.get_signals itself) never round-trips through here — the
frontend already synthesizes a session's own opening transition from
start_state alone (see ChatTimeline.js's syntheticSessionStartEntry),
so there's nothing lost by leaving it out.
"""
from __future__ import annotations

import json

from db import Db
from db.utils import _utc_iso


class SessionExportManager:
    def __init__(self, db: Db) -> None:
        self._db = db

    def export_sessions(self, username: str, project_name: str) -> list[dict]:
        sessions = self._db.list_chat_sessions(username, project_name, source=('native', 'imported'))
        return [self._export_session(session) for session in sessions]

    def _export_session(self, session: dict) -> dict:
        session_id = session['id']
        tracking_by_message = {
            row['message_id']: row
            for row in self._db.get_signals(session_id)
            if row['message_id'] is not None
        }
        return {
            'name': session['title'],
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
