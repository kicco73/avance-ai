"""Shared by TestProcessor (deciding which message a tracking row links to)
and BatchLiteSignalSource (deciding which message's content represents a
turn) — both need the same "what comes right after this user message"
lookup."""
from __future__ import annotations


def next_assistant_message_id(ordered_ids: list[int], by_id: dict, user_message_id: int) -> int | None:
    index = ordered_ids.index(user_message_id)
    if index + 1 < len(ordered_ids):
        next_id = ordered_ids[index + 1]
        if by_id[next_id]['role'] == 'assistant':
            return next_id
    return None
