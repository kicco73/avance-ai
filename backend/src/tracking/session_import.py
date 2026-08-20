"""Importing a chat session — from a plain-text transcript (source
'imported', no Tracking rows at all: no estimate has ever run over one
until a Test does), or from the richer JSON shape session_export.py's own
SessionExportManager produces (source 'imported' too, but with every
message's own linked Tracking row restored alongside it — see
import_session_json). Both are TrackingService.import_session/import_
session_json's own owner."""
from __future__ import annotations

import re

from db import Db
from db.utils import _parse_iso

# A line opens a new message when it starts (ignoring leading whitespace)
# with "user:" or "assistant:", case-insensitive; at most one space right
# after the colon is dropped from that message's own first line of
# content.
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

    def import_transcript(self, username: str, project_name: str, text: str, title: str | None = None) -> int:
        messages = parse_transcript(text)
        session_id = self._db.create_chat_session(
            username, project_name,
            datetime_start=None, datetime_end=None, start_state=None, end_state=None,
            source='imported', title=title,
        )
        for message in messages:
            self._db.save_message(message["role"], message["content"], session_id, timestamp=None)
        return session_id

    def import_session_json(self, username: str, project_name: str, session_data: dict) -> int:
        """Restores one session_export.py-produced session object exactly
        — ChatSession + Message rows + whichever Tracking row each
        message originally carried (see SessionExportManager's own
        docstring on the inline shape). Always `source='imported'`,
        regardless of what the session originally was: a round-tripped
        session never ran against *this* automaton/revision, same
        reasoning import_transcript already follows for a plain
        transcript. Raises KeyError/TypeError on a malformed `messages`
        entry — the caller (TrackingService.import_session_json) is the
        one that turns that into a real 4xx."""
        messages = session_data.get('messages', [])
        session_id = self._db.create_chat_session(
            username, project_name,
            datetime_start=_parse_iso(session_data.get('timestamp')),
            datetime_end=_parse_iso(session_data.get('datetime_end')),
            start_state=session_data.get('start_state'),
            end_state=session_data.get('end_state'),
            source='imported', title=session_data.get('name'),
        )
        if session_data.get('labeled'):
            self._db.set_session_labeled(session_id, True)
        if session_data.get('comment'):
            self._db.set_session_comment(session_id, session_data['comment'])
        for message in messages:
            self._import_message(session_id, message)
        return session_id

    # Every one of these is optional on a message entry (see session_
    # export.py's own _export_message: only present at all when the
    # message had a linked Tracking row to begin with) — a plain
    # `message.get(key)` for each, rather than requiring the caller's
    # JSON to carry every key on every message.
    _TRACKING_FIELDS = ('old_state', 'action', 'new_state', 'values', 'expected_state', 'expected_values', 'comment')

    def _import_message(self, session_id: int, message: dict) -> None:
        message_id = self._db.save_message(
            message['role'], message['text'], session_id,
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
