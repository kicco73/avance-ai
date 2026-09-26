from __future__ import annotations

from typing import Any

import pandas as pd

MESSAGE_COLUMNS = ["id", "role", "content", "audio_text", "timestamp", "expected_state", "session_id"]


class SessionSteps(object):
    """Every session's messages and action entries in one ordered id
    space: a step id replaces a message id wherever the benchmark frames
    carry one, so an action entry is a point in its own place."""

    def __init__(self) -> None:
        self._next = 1
        self._by_message: dict[int, int] = {}
        self._by_entry: dict[int, int] = {}
        self._rows: list[dict[str, Any]] = []

    def add_session(self, session_id: int, messages: list[dict], tracking_rows: list[dict]) -> None:
        expected_by_message = {
            row["message_id"]: row["expected_state"] for row in tracking_rows if row["message_id"] is not None
        }
        entries = sorted(
            (row for row in tracking_rows if row.get("position") is not None),
            key=lambda row: (row["position"], row["id"]),
        )
        for index, message in enumerate(messages):
            while entries and entries[0]["position"] <= index:
                self._add_entry(session_id, entries.pop(0))
            self._by_message[message["id"]] = self._next
            self._rows.append({
                **message, "id": self._take(), "expected_state": expected_by_message.get(message["id"]),
                "session_id": session_id,
            })
        for entry in entries:
            self._add_entry(session_id, entry)

    def _add_entry(self, session_id: int, entry: dict) -> None:
        self._by_entry[entry["id"]] = self._next
        self._rows.append({
            "id": self._take(), "role": "action", "content": "", "audio_text": None,
            "timestamp": entry["timestamp"], "expected_state": entry["expected_state"], "session_id": session_id,
        })

    def _take(self) -> int:
        step = self._next
        self._next += 1
        return step

    def messages_frame(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=MESSAGE_COLUMNS)
        frame = pd.DataFrame.from_records(self._rows)
        for column in MESSAGE_COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
        return frame[MESSAGE_COLUMNS].sort_values(["session_id", "id"], kind="stable")

    def rekeyed(self, row: dict, entry_id: int | None) -> dict:
        """`row` with its message_id replaced by the step it belongs to:
        the action entry `entry_id` names, else its own message."""
        step = self._by_entry.get(entry_id) if entry_id is not None else None
        if step is None and row.get("message_id") is not None:
            step = self._by_message.get(int(row["message_id"]))
        return {**row, "message_id": step}
