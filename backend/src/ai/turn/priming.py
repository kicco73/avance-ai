"""Builds the never-persisted turn carrying attachments as provider-
neutral 'attachment' blocks. Shared, dependency-free home for this so
both TurnService (every kind of turn) and AutoTracker can use it
without importing each other.
"""
from __future__ import annotations


def build_priming_messages(attachments: list) -> list[dict]:
    if not attachments:
        return []
    return [
        {
            "role": "user",
            "content": [
                {"type": "attachment", "filename": a.filename, "source": a.source}
                for a in attachments
            ],
        },
        {"role": "assistant", "content": "Understood."},
    ]
