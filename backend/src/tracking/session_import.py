"""Importing a chat session — from a plain-text transcript (source
'imported', no Tracking rows at all), or from the richer JSON shape
SessionExportManager produces (source 'imported' too, but with each
message's linked Tracking row restored alongside it)."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from db import Db
from db.utils import _parse_iso
from schemas import SessionImportJsonRequest

# A line opens a new message when it starts with "user:" or "assistant:"
# (case-insensitive, leading whitespace ignored); at most one space
# after the colon is dropped from the first line of content.
_PREFIX_RE = re.compile(r"^\s*(user|assistant):[ \t]?(.*)$", re.IGNORECASE)


def parse_transcript(text: str) -> list[dict]:
    lines = text.splitlines()
    if not lines or not _PREFIX_RE.match(lines[0]):
        raise ValueError("Transcript must start with a line beginning 'user:' or 'assistant:'.")

    raw_messages: list[dict] = []
    for line in lines:
        match = _PREFIX_RE.match(line)
        if match is not None:
            raw_messages.append({"role": match.group(1).lower(), "lines": [match.group(2)]})
        else:
            raw_messages[-1]["lines"].append(line)

    if not raw_messages:
        raise ValueError("No valid messages found in transcript.")

    # Consecutive same-role raw messages merge into one — the final
    # result always alternates user/assistant.
    merged: list[dict] = []
    for message in raw_messages:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["lines"].extend(message["lines"])
        else:
            merged.append({"role": message["role"], "lines": list(message["lines"])})

    result: list[dict] = []
    for message in merged:
        content_lines = list(message["lines"])
        while content_lines and content_lines[0].strip() == "":
            content_lines.pop(0)
        while content_lines and content_lines[-1].strip() == "":
            content_lines.pop()
        content = "\n".join(content_lines) or "…"
        result.append({"role": message["role"], "content": content})

    return result


class SessionImportManager:
    def __init__(self, db: Db) -> None:
        self._db = db

    def _published_revision(self, project_name: str) -> int:
        revision = self._db.get_project_published_revision(project_name)
        if revision is None:
            raise ValueError(f"Project '{project_name}' has never been published.")
        return revision

    def import_transcript(self, username: str, project_name: str, text: str, title: str | None = None) -> int:
        messages = parse_transcript(text)
        session_id = self._db.create_chat_session(
            username, project_name, self._published_revision(project_name),
            datetime_start=None, datetime_end=None, start_state=None, end_state=None,
            type='imported', title=title,
        )
        for message in messages:
            self._db.save_message(message["role"], message["content"], session_id, timestamp=None)
        return session_id

    def import_session_json(self, username: str, project_name: str, session_data: dict) -> int:
        datetime_start = _parse_iso(session_data.get('timestamp'))
        datetime_end = _parse_iso(session_data.get('datetime_end'))
        if datetime_start is not None and datetime_end is not None and self._db.chat_session_exists(
            username, project_name, datetime_start, datetime_end
        ):
            raise ValueError('A session with the same start and end time already exists for this user.')
        restored_type = session_data.get('type') if session_data.get('type') in ('live', 'imported') else 'imported'
        messages = session_data.get('messages', [])
        session_id = self._db.create_chat_session(
            username, project_name, self._published_revision(project_name),
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            start_state=session_data.get('start_state'),
            end_state=session_data.get('end_state'),
            type=restored_type, title=session_data.get('name'),
        )
        try:
            if session_data.get('labeled'):
                self._db.set_session_labeled(session_id, True)
            if session_data.get('comment'):
                self._db.set_session_comment(session_id, session_data['comment'])
            for message in messages:
                self._import_message(session_id, message)
        except (KeyError, TypeError):
            # No transaction of its own — cleaned up by hand instead, so a
            # malformed session never leaves a message-less ChatSession row
            # behind for a retrying caller to mistake for a genuine one.
            self._db.delete_chat_session(session_id)
            raise
        return session_id

    def import_batch(self, project_name: str, uploads: list[tuple[str, bytes]]) -> dict:
        results: list[dict] = []
        last_session_id: int | None = None
        transcript_test_user: str | None = None
        for filename, content in uploads:
            if (filename or '').lower().endswith('.json'):
                session_id = self._import_json_upload(project_name, filename, content, results)
            else:
                if transcript_test_user is None:
                    transcript_test_user = self._db.next_test_user_username(project_name)
                session_id = self._import_transcript_upload(transcript_test_user, project_name, filename, content, results)
            if session_id is not None:
                last_session_id = session_id
        return {'results': results, 'last_session_id': last_session_id}

    def _import_transcript_upload(
        self, username: str, project_name: str, filename: str, content: bytes, results: list[dict],
    ) -> int | None:
        try:
            session_id = self.import_transcript(username, project_name, content.decode('utf-8'), title=filename)
            results.append({'file': filename, 'ok': True, 'session_id': session_id})
            return session_id
        except (ValueError, UnicodeDecodeError) as exc:
            results.append({'file': filename, 'ok': False, 'error': str(exc)})
            return None

    def _import_json_upload(
        self, project_name: str, filename: str, content: bytes, results: list[dict],
    ) -> int | None:
        try:
            parsed = json.loads(content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            results.append({'file': filename, 'ok': False, 'error': f'Invalid JSON: {exc}'})
            return None
        if not isinstance(parsed, list):
            results.append({'file': filename, 'ok': False, 'error': 'Expected a JSON array of sessions.'})
            return None

        last_session_id = None
        for index, session_data in enumerate(parsed):
            label = session_data.get('name') if isinstance(session_data, dict) else None
            label = label or f'{filename} #{index + 1}'
            try:
                validated = SessionImportJsonRequest(**session_data)
                username = validated.username or self._db.next_test_user_username(project_name)
                session_id = self.import_session_json(username, project_name, validated.model_dump())
                results.append({'file': label, 'ok': True, 'session_id': session_id})
                last_session_id = session_id
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                results.append({'file': label, 'ok': False, 'error': str(exc)})
        return last_session_id

    # Every one of these is optional on a message entry — a plain
    # `message.get(key)` for each, rather than requiring the caller's
    # JSON to carry every key on every message.
    _TRACKING_FIELDS = ('old_state', 'action', 'new_state', 'values', 'expected_state', 'expected_values', 'comment')

    def _import_message(self, session_id: int, message: dict) -> None:
        text = message['text']
        if message['role'] == 'assistant' and not text:
            text = '…'
        message_id = self._db.save_message(
            message['role'], text, session_id,
            audio_text=message.get('audio_text'),
            timestamp=_parse_iso(message.get('timestamp')),
        )
        if not any(message.get(field) is not None for field in self._TRACKING_FIELDS):
            return
        self._db.import_tracking_row(
            session_id,
            old_state=message.get('old_state'), action=message.get('action'), new_state=message.get('new_state'),
            values=message.get('values'), expected_state=message.get('expected_state'),
            expected_values=message.get('expected_values'), comment=message.get('comment'),
            message_id=message_id, timestamp=_parse_iso(message.get('timestamp')),
        )
