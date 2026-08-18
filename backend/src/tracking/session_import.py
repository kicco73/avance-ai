"""Importing a chat session from a plain-text transcript — creates a
`ChatSession`/`Message` set indistinguishable in shape from a live one,
but with `source='imported'` and no `Tracking` rows at all (no estimate
has ever run over an imported transcript until a Test does — see
TrackingService.import_session, this module's own owner)."""
from __future__ import annotations

import re

from db import Db

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

    def import_transcript(self, username: str, project_name: str, text: str) -> int:
        messages = parse_transcript(text)
        session_id = self._db.create_chat_session(
            username, project_name,
            datetime_start=None, datetime_end=None, start_state=None, end_state=None,
            source='imported',
        )
        for message in messages:
            self._db.save_message(message["role"], message["content"], session_id, timestamp=None)
        return session_id
