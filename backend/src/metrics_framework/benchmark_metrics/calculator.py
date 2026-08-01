from __future__ import annotations

import json
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
        self._db = db
        self._username = username
        self._project_name = project_name
        self._session_id = session_id
        self._configuration = configuration or BenchmarkConfiguration()
        self._metrics = tuple(metrics) if metrics is not None else self.default_metrics()

    def default_metrics(self) -> tuple[BenchmarkMetric, ...]:
        return (
            StateAccuracyMetric(),
            SignalAccuracyMetric(),
            TransitionResponsivenessMetric(),
            BenchmarkAccuracyMetric(),
            BenchmarkStabilityMetric(),
            BenchmarkConsistencyMetric(self._configuration),
        )

    def calculate_all(self) -> list[BenchmarkMetricResult]:
        observations = self._build_observations()
        return [metric.calculate(observations) for metric in self._metrics]

    def calculate(self, metric: BenchmarkMetric) -> BenchmarkMetricResult:
        return metric.calculate(self._build_observations())

    def observations(self):
        return self._build_observations()

    def _build_observations(self):
        sessions = self._load_sessions()
        session_ids = [int(row["id"]) for row in sessions]
        messages = self._load_messages(session_ids)
        signals = self._load_signals(session_ids)
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
        sessions = self._db.list_chat_sessions(self._username, self._project_name)
        if self._session_id is None:
            return sessions
        return [row for row in sessions if int(row["id"]) == self._session_id]

    def _load_messages(self, session_ids: list[int]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for session_id in session_ids:
            rows.extend(self._db.get_messages(session_id))
        columns = [
            "id", "role", "content", "audio_text", "timestamp", "expected_state", "expected_values", "session_id"
        ]
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame.from_records(rows)
        frame["session_id"] = [int(row.get("session_id", 0)) for row in rows]
        for column in columns:
            if column not in frame.columns:
                frame[column] = None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame[columns].sort_values(["session_id", "timestamp", "id"], kind="stable")

    def _load_signals(self, session_ids: list[int]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for session_id in session_ids:
            for row in self._db.get_signals(session_id):
                copied = dict(row)
                copied["session_id"] = session_id
                rows.append(copied)
        columns = ["id", "timestamp", "values", "expected_values", "old_state", "action", "new_state", "session_id"]
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame.from_records(rows)
        for column in columns:
            if column not in frame.columns:
                frame[column] = None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame[columns].sort_values(["session_id", "timestamp", "id"], kind="stable")

    @staticmethod
    def _frame(rows: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame.from_records(rows, columns=columns)

    @staticmethod
    def _empty_signals() -> pd.DataFrame:
        return pd.DataFrame(columns=["id", "timestamp", "values", "expected_values", "old_state", "action", "new_state", "session_id"])
