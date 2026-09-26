"""Importing a chat session — from a plain-text transcript (source
'imported', no Tracking rows at all), or from the richer JSON shape
SessionExportManager produces (source 'imported' too, but with each
message's linked Tracking row restored alongside it)."""
from __future__ import annotations

import re

from db import Db, validated_role
from db.utils import _parse_iso
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

    def _published_revision(self, project_id: str) -> int:
        revision = self._db.get_project_published_revision(project_id)
        if revision is None:
            raise ValueError(f"Project '{project_id}' has never been published.")
        return revision

    def import_transcript(self, username: str, project_id: str, text: str, title: str | None = None) -> int:
        messages = parse_transcript(text)
        session_id = self._db.create_chat_session(
            username, project_id, self._published_revision(project_id),
            datetime_start=None, datetime_end=None, start_state=None, end_state=None,
            type='imported', title=title,
        )
        for message in messages:
            self._db.save_message(message["role"], message["content"], session_id, timestamp=None)
        return session_id

    def import_session_json(self, username: str, project_id: str, session_data: dict) -> int:
        datetime_start = _parse_iso(session_data.get('timestamp'))
        datetime_end = _parse_iso(session_data.get('datetime_end'))
        if datetime_start is not None and datetime_end is not None and self._db.chat_session_exists(
            username, project_id, datetime_start, datetime_end
        ):
            raise ValueError('A session with the same start and end time already exists for this user.')
        restored_type = session_data.get('type') if session_data.get('type') in ('live', 'imported') else 'imported'
        messages = session_data.get('messages', [])
        session_id = self._db.create_chat_session(
            username, project_id, self._published_revision(project_id),
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            start_state=session_data.get('start_state'),
            end_state=session_data.get('end_state'),
            type=restored_type, title=session_data.get('name'),
            channel=session_data.get('channel'),
            closed_at=_parse_iso(session_data.get('closed_at')),
            close_reason=session_data.get('close_reason'),
        )
        try:
            if session_data.get('labeled'):
                self._db.set_session_labeled(session_id, True)
            if session_data.get('comment'):
                self._db.set_session_comment(session_id, session_data['comment'])
            imported = 0
            for message in messages:
                if message.get('role') == 'action':
                    self._import_action(session_id, message, imported)
                    continue
                self._import_message(session_id, message)
                imported += 1
        except (KeyError, TypeError, ValueError):
            self._db.delete_chat_session(session_id)
            raise
        return session_id
    def _import_action(self, session_id: int, entry: dict, position: int) -> None:
        action, choice = entry.get('action'), entry.get('choice')
        if (action is None) == (choice is None):
            raise ValueError("An 'action' entry names exactly one of 'action' or 'choice'.")
        if choice is not None and not (isinstance(choice, dict) and choice.get('key') and choice.get('option') is not None):
            raise ValueError("An 'action' entry's 'choice' is {'key': ..., 'option': ...}.")
        self._db.import_tracking_row(
            session_id,
            old_state=entry.get('old_state'), action=action, new_state=entry.get('new_state'),
            values=entry.get('values'), expected_state=entry.get('expected_state'), expected_values=None,
            comment=entry.get('comment'), message_id=None, timestamp=_parse_iso(entry.get('timestamp')),
            origin='manual', position=position,
            choice={'key': choice['key'], 'option': choice['option']} if choice is not None else None,
        )

    _TRACKING_FIELDS = ('old_state', 'action', 'new_state', 'values', 'expected_state', 'expected_values', 'comment', 'origin')

    def _import_message(self, session_id: int, message: dict) -> None:
        role = validated_role(message['role'])
        text = message.get('text')
        if text is None:
            raise ValueError(f"A '{role}' message needs its 'text'.")
        if role == 'assistant' and not text:
            text = '…'
        message_id = self._db.save_message(
            role, text, session_id,
            audio_text=message.get('audio_text'),
            tokens=message.get('tokens'),
            timestamp=_parse_iso(message.get('timestamp')),
        )
        if message.get('tool_calls') is not None:
            self._db.record_tool_calls(
                session_id, message['tool_calls'], message_id=message_id, timestamp=_parse_iso(message.get('timestamp')),
            )
        if not any(message.get(field) is not None for field in self._TRACKING_FIELDS):
            return
        self._db.import_tracking_row(
            session_id,
            old_state=message.get('old_state'), action=message.get('action'), new_state=message.get('new_state'),
            values=message.get('values'), expected_state=message.get('expected_state'),
            expected_values=message.get('expected_values'), comment=message.get('comment'),
            message_id=message_id, timestamp=_parse_iso(message.get('timestamp')),
            origin=message.get('origin'),
        )
