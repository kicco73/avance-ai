"""Builds the never-persisted turn carrying attachments as provider-
neutral 'attachment' blocks. Shared, dependency-free home for this so
both ChatService (every kind of turn) and AutoTracker (its fallback
call into Signals.compute()) can use it without importing each other.
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
