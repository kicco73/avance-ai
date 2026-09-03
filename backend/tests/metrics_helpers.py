"""Shared DataFrame-building helpers for metrics_framework tests. Mirrors
the column shapes UserAnalyticsDataBuilder produces, so metric-level
tests can construct a UserAnalyticsData directly, without a database."""
from __future__ import annotations

import json

import pandas as pd

from metrics.metrics_framework.dto import UserAnalyticsData

SESSION_COLUMNS = ["id", "username", "project_id", "datetime_start", "datetime_end", "start_state", "end_state"]
MESSAGE_COLUMNS = ["id", "role", "content", "audio_text", "timestamp"]
SIGNAL_COLUMNS = ["id", "timestamp", "values", "old_state", "action", "new_state", "message_id"]


def messages_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=MESSAGE_COLUMNS)
    frame = pd.DataFrame.from_records(rows, columns=MESSAGE_COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def sessions_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=SESSION_COLUMNS)
    return pd.DataFrame.from_records(rows, columns=SESSION_COLUMNS)


def signals_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    frame = pd.DataFrame.from_records(rows, columns=SIGNAL_COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def analytics_data(*, messages=None, sessions=None, signals=None, transitions=None) -> UserAnalyticsData:
    return UserAnalyticsData(
        username="user",
        project_id="proj",
        messages=messages_frame(messages or []),
        sessions=sessions_frame(sessions or []),
        signals=signals_frame(signals or []),
        transitions=signals_frame(transitions or []),
    )


def message_row(id_, role, ts, content="hi") -> dict:
    return {"id": id_, "role": role, "content": content, "audio_text": None, "timestamp": ts}


def session_row(id_, start, end, start_state="a", end_state="a") -> dict:
    return {
        "id": id_,
        "username": "user",
        "project_id": "proj",
        "datetime_start": start,
        "datetime_end": end,
        "start_state": start_state,
        "end_state": end_state,
    }


def signal_row(id_, ts, values=None, old_state=None, action=None, new_state=None, message_id=None) -> dict:
    return {
        "id": id_,
        "timestamp": ts,
        "values": json.dumps(values) if values is not None else None,
        "old_state": old_state,
        "action": action,
        "new_state": new_state,
        # Defaults to id_ itself, matching ascending chronological order
        # for Timeline.signal_series's message_id-based ordering.
        "message_id": id_ if message_id is None else message_id,
    }
