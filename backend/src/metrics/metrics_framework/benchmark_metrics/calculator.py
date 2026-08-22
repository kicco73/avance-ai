from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from .dto import BenchmarkConfiguration, BenchmarkMetricResult
from .interfaces import BenchmarkMetric
from .metrics import (
    BenchmarkAccuracyMetric,
    BenchmarkConsistencyMetric,
    BenchmarkStabilityMetric,
    SignalAccuracyMetric,
    StateAccuracyMetric,
    StateAccuracyStableMetric,
    StateAccuracyTransitionMetric,
    TransitionResponsivenessMetric,
)
from .observations import BenchmarkData, BenchmarkObservationBuilder


class BenchmarkCalculator(object):
    """Public facade for expert-ground-truth benchmark metrics."""

    def __init__(
        self,
        db: Any,
        username: str,
        project_name: str,
        configuration: BenchmarkConfiguration | None = None,
        session_id: int | None = None,
        metrics: Iterable[BenchmarkMetric] | None = None,
    ) -> None:
        """The default metric set is filtered to whatever's meaningful in
        a "one_session" context; an explicitly passed `metrics` is used
        as-is, unfiltered — the caller's own explicit choice."""
        self._db = db
        self._username = username
        self._project_name = project_name
        self._session_id = session_id
        self._configuration = configuration or BenchmarkConfiguration()
        # None here tells _build_observations to load from `db` normally;
        # from_data below sets this instead.
        self._data: BenchmarkData | None = None
        self._metrics = self._select_metrics(metrics)

    def _select_metrics(self, metrics: Iterable[BenchmarkMetric] | None) -> tuple[BenchmarkMetric, ...]:
        if metrics is not None:
            return tuple(metrics)
        return tuple(m for m in self.default_metrics() if "one_session" in m.scope)

    @classmethod
    def from_data(
        cls,
        data: BenchmarkData,
        configuration: BenchmarkConfiguration | None = None,
        metrics: Iterable[BenchmarkMetric] | None = None,
    ) -> "BenchmarkCalculator":
        """Builds directly from an already-ready BenchmarkData, skipping
        the usual DB round-trips. Same scope filter as the normal
        constructor: `metrics` explicit if given, else "one_session" defaults."""
        instance = cls.__new__(cls)
        instance._configuration = configuration or BenchmarkConfiguration()
        instance._data = data
        instance._metrics = instance._select_metrics(metrics)
        return instance

    def default_metrics(self) -> tuple[BenchmarkMetric, ...]:
        return (
            StateAccuracyMetric(),
            StateAccuracyStableMetric(),
            StateAccuracyTransitionMetric(),
            SignalAccuracyMetric(),
            TransitionResponsivenessMetric(),
            BenchmarkAccuracyMetric(),
            BenchmarkStabilityMetric(),
            BenchmarkConsistencyMetric(self._configuration),
        )

    @property
    def metrics(self) -> tuple[BenchmarkMetric, ...]:
        """The metric instances calculate_all() evaluates, in the same
        order as its own results — lets a caller pair each
        BenchmarkMetricResult with its own name/ui_label/ui_description."""
        return self._metrics

    def calculate_all(self) -> list[BenchmarkMetricResult]:
        observations = self._build_observations()
        return [metric.calculate(observations) for metric in self._metrics]

    def calculate(self, metric: BenchmarkMetric) -> BenchmarkMetricResult:
        return metric.calculate(self._build_observations())

    def observations(self):
        return self._build_observations()

    def _build_observations(self):
        if self._data is not None:
            return BenchmarkObservationBuilder(self._configuration).build(self._data)
        sessions = self._load_sessions()
        session_ids = [int(row["id"]) for row in sessions]
        # Tracking rows loaded once per session and reused for both frames
        # (expected_state lives on the Tracking row, not the message) —
        # refetching per frame would double the db calls for the same rows.
        signal_rows_by_session = {session_id: self._db.get_signals(session_id) for session_id in session_ids}
        messages = self._load_messages(session_ids, signal_rows_by_session)
        signals = self._load_signals(session_ids, signal_rows_by_session)
        data = BenchmarkData(
            messages=messages,
            sessions=self._frame(sessions, [
                "id", "username", "project_name", "datetime_start", "datetime_end", "start_state", "end_state"
            ]),
            signals=signals,
            transitions=signals.loc[signals["new_state"].notna()].copy() if not signals.empty else self._empty_signals(),
        )
        return BenchmarkObservationBuilder(self._configuration).build(data)

    def _load_sessions(self) -> list[dict[str, object]]:
        # type=None: benchmark metrics compare expert annotations against
        # replayed behaviour regardless of a session's origin — list_chat_sessions'
        # own default (type='live') would silently drop every imported session,
        # which is exactly where annotations usually live.
        sessions = self._db.list_chat_sessions(self._username, self._project_name, type=None)
        if self._session_id is None:
            return sessions
        return [row for row in sessions if int(row["id"]) == self._session_id]

    def _load_messages(
        self, session_ids: list[int], signal_rows_by_session: dict[int, list[dict[str, Any]]]
    ) -> pd.DataFrame:
        columns = ["id", "role", "content", "audio_text", "timestamp", "expected_state", "session_id"]
        rows: list[dict[str, Any]] = []
        for session_id in session_ids:
            expected_state_by_message = {
                row["message_id"]: row["expected_state"]
                for row in signal_rows_by_session[session_id]
                if row["message_id"] is not None
            }
            for message in self._db.get_messages(session_id):
                rows.append({**message, "expected_state": expected_state_by_message.get(message["id"])})
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame.from_records(rows)
        frame["session_id"] = [int(row.get("session_id", 0)) for row in rows]
        for column in columns:
            if column not in frame.columns:
                frame[column] = None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame[columns].sort_values(["session_id", "timestamp", "id"], kind="stable")

    def _load_signals(
        self, session_ids: list[int], signal_rows_by_session: dict[int, list[dict[str, Any]]]
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for session_id in session_ids:
            for row in signal_rows_by_session[session_id]:
                copied = dict(row)
                copied["session_id"] = session_id
                rows.append(copied)
        columns = ["id", "message_id", "timestamp", "values", "expected_values", "old_state", "action", "new_state", "session_id"]
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame.from_records(rows)
        for column in columns:
            if column not in frame.columns:
                frame[column] = None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame[columns].sort_values(["session_id", "id"], kind="stable")

    @staticmethod
    def _frame(rows: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame.from_records(rows, columns=columns)

    @staticmethod
    def _empty_signals() -> pd.DataFrame:
        return pd.DataFrame(columns=["id", "message_id", "timestamp", "values", "expected_values", "old_state", "action", "new_state", "session_id"])
