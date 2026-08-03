from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from .dto import UserAnalyticsData
from .interfaces import AnalyticsDb


class UserAnalyticsDataBuilder(object):
    """Builds the analytical dataset from the application's Db facade.

    The builder intentionally does not import Peewee models. The Db facade
    remains the single point of database access.
    """

    def __init__(self, db: AnalyticsDb, username: str, project_name: str) -> None:
        self._db = db
        self._username = username
        self._project_name = project_name

    def build(self, until: datetime | None = None) -> UserAnalyticsData:
        """`until`, when given, restricts every dataset to what existed at
        or before that point in time — sessions that hadn't started yet,
        and messages/signals timestamped after it, are excluded. This is
        what lets a caller compute metrics "as of" a specific past
        message (see chat/metrics_service.py's calculate_values_until)
        instead of always the full, current history."""
        sessions = self._db.list_chat_sessions(self._username, self._project_name)
        if until is not None:
            sessions = [row for row in sessions if row["datetime_start"] <= until]
        session_ids = [int(row["id"]) for row in sessions]

        messages = self._load_messages(session_ids)
        signals = self._load_signals(session_ids)
        if until is not None:
            until_ts = pd.Timestamp(until, tz="UTC") if until.tzinfo is None else pd.Timestamp(until)
            if not messages.empty:
                messages = messages.loc[messages["timestamp"] <= until_ts].copy()
            if not signals.empty:
                signals = signals.loc[signals["timestamp"] <= until_ts].copy()

        if signals.empty:
            transitions = self._empty_transitions()
            signal_snapshots = self._empty_signals()
        else:
            transition_mask = signals["new_state"].notna()
            transitions = signals.loc[transition_mask].copy()
            signal_snapshots = signals.loc[~transition_mask].copy()

        return UserAnalyticsData(
            username=self._username,
            project_name=self._project_name,
            messages=messages,
            sessions=self._frame(sessions, [
                "id", "username", "project_name", "datetime_start",
                "datetime_end", "start_state", "end_state",
            ]),
            signals=signal_snapshots,
            transitions=transitions,
        )

    def _load_messages(self, session_ids: list[int]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for session_id in session_ids:
            rows.extend(self._db.get_messages(session_id))
        frame = self._frame(rows, ["id", "role", "content", "audio_text", "timestamp"])
        if not frame.empty:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            frame.sort_values("timestamp", inplace=True, kind="stable")
        return frame

    def _load_signals(self, session_ids: list[int]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for session_id in session_ids:
            rows.extend(self._db.get_signals(session_id))
        frame = self._frame(
            rows,
            ["id", "timestamp", "values", "old_state", "action", "new_state"],
        )
        if not frame.empty:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            frame.sort_values("timestamp", inplace=True, kind="stable")
        return frame

    @staticmethod
    def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame.from_records(rows, columns=columns)

    @staticmethod
    def _empty_signals() -> pd.DataFrame:
        return pd.DataFrame(columns=["id", "timestamp", "values", "old_state", "action", "new_state"])

    @staticmethod
    def _empty_transitions() -> pd.DataFrame:
        return pd.DataFrame(columns=["id", "timestamp", "values", "old_state", "action", "new_state"])


class Timeline(object):
    """Convenience operations over the shared analytical data."""

    def __init__(self, data: UserAnalyticsData) -> None:
        self._data = data

    def signal_series(self, signal_name: str) -> pd.Series:
        if self._data.signals.empty:
            return pd.Series(dtype="float64", name=signal_name)

        values: list[tuple[pd.Timestamp, float]] = []
        for timestamp, raw_values in zip(
            self._data.signals["timestamp"], self._data.signals["values"]
        ):
            parsed = self._parse_values(raw_values)
            value = parsed.get(signal_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append((timestamp, float(value)))

        if not values:
            return pd.Series(dtype="float64", name=signal_name)

        series = pd.Series(
            [value for _, value in values],
            index=pd.DatetimeIndex([timestamp for timestamp, _ in values]),
            name=signal_name,
        )
        return series.sort_index()

    @staticmethod
    def _parse_values(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        return {}
