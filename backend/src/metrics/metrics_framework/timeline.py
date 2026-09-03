from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from .dto import UserAnalyticsData
from .interfaces import AnalyticsDb


class UserAnalyticsDataBuilder(object):
    """Builds the analytical dataset from the application's Db facade. Does
    not import Peewee models directly — the Db facade stays the single
    point of database access."""

    def __init__(self, db: AnalyticsDb, username: str, project_id: str) -> None:
        self._db = db
        self._username = username
        self._project_id = project_id

    def build(self, since: datetime | None = None, until: datetime | None = None) -> UserAnalyticsData:
        """`since`/`until`, each independently optional, restrict every
        dataset to what falls within [since, until]. `until` alone computes
        metrics "as of" a past message; `since` alongside it bounds to a window."""
        sessions = self._db.list_chat_sessions(self._username, self._project_id)
        if since is not None:
            sessions = [row for row in sessions if row["datetime_start"] >= since]
        if until is not None:
            sessions = [row for row in sessions if row["datetime_start"] <= until]
        session_ids = [int(row["id"]) for row in sessions]

        messages = self._load_messages(session_ids)
        signals = self._load_signals(session_ids)
        messages = self._filter_by_timestamp(messages, since, until)
        signals = self._filter_by_timestamp(signals, since, until)

        return self._assemble(sessions, messages, signals)

    @staticmethod
    def _filter_by_timestamp(
        frame: pd.DataFrame, since: datetime | None, until: datetime | None,
    ) -> pd.DataFrame:
        if frame.empty or (since is None and until is None):
            return frame
        if since is not None:
            since_ts = pd.Timestamp(since, tz="UTC") if since.tzinfo is None else pd.Timestamp(since)
            frame = frame.loc[frame["timestamp"] >= since_ts]
        if until is not None:
            until_ts = pd.Timestamp(until, tz="UTC") if until.tzinfo is None else pd.Timestamp(until)
            frame = frame.loc[frame["timestamp"] <= until_ts]
        return frame.copy()

    def build_for_session(self, session_id: int, until_message_id: int | None = None) -> UserAnalyticsData:
        """Like build(), but scoped to exactly one session, never the
        user's whole cross-session history. `until_message_id` truncates
        by id rather than timestamp, since an imported session may have no real timestamps."""
        session = self._db.get_chat_session(session_id)
        sessions = [session] if session is not None else []

        messages = self._load_messages([session_id])
        signals = self._load_signals([session_id])
        if until_message_id is not None:
            if not messages.empty:
                messages = messages.loc[messages["id"] <= until_message_id].copy()
            if not signals.empty:
                signals = signals.loc[signals["message_id"] <= until_message_id].copy()

        return self._assemble(sessions, messages, signals)

    def _assemble(
        self, sessions: list[dict[str, Any]], messages: pd.DataFrame, signals: pd.DataFrame,
    ) -> UserAnalyticsData:
        if signals.empty:
            transitions = self._empty_transitions()
            signal_snapshots = self._empty_signals()
        else:
            transition_mask = signals["new_state"].notna()
            transitions = signals.loc[transition_mask].copy()
            signal_snapshots = signals.loc[~transition_mask].copy()

        return UserAnalyticsData(
            username=self._username,
            project_id=self._project_id,
            messages=messages,
            sessions=self._frame(sessions, [
                "id", "username", "project_id", "datetime_start",
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
            ["id", "timestamp", "values", "old_state", "action", "new_state", "message_id"],
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
        return pd.DataFrame(columns=["id", "timestamp", "values", "old_state", "action", "new_state", "message_id"])

    @staticmethod
    def _empty_transitions() -> pd.DataFrame:
        return pd.DataFrame(columns=["id", "timestamp", "values", "old_state", "action", "new_state", "message_id"])


class Timeline(object):
    """Convenience operations over the shared analytical data."""

    def __init__(self, data: UserAnalyticsData) -> None:
        self._data = data

    def signal_series(self, signal_name: str) -> pd.Series:
        """Indexed/ordered by message_id, not timestamp — this measures
        variation between consecutive values, which only needs correct
        event order, never real elapsed time."""
        if self._data.signals.empty:
            return pd.Series(dtype="float64", name=signal_name)

        values: list[tuple[int, float]] = []
        for message_id, raw_values in zip(
            self._data.signals["message_id"], self._data.signals["values"]
        ):
            parsed = self._parse_values(raw_values)
            value = parsed.get(signal_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append((message_id, float(value)))

        if not values:
            return pd.Series(dtype="float64", name=signal_name)

        series = pd.Series(
            [value for _, value in values],
            index=pd.Index([message_id for message_id, _ in values], name="message_id"),
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
