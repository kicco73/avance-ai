from __future__ import annotations

from typing import Any

import pandas as pd

from db import Db
from metrics.metrics_framework.timeline import records_frame
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkData


class TestDataBuilder:
    """Assembles one test run's BenchmarkData from the real session/message
    rows plus that run's own TestObservation rows."""

    _SIGNALS_COLUMNS = ["id", "message_id", "timestamp", "values", "expected_values", "old_state", "action", "new_state", "session_id"]

    @classmethod
    def build(cls, db: Db, run: dict) -> BenchmarkData:
        calculator = BenchmarkCalculator(db, run['username'], run['project_id'], session_id=run['session_id'])

        sessions_rows = calculator._load_sessions()
        session_ids = [int(row['id']) for row in sessions_rows]
        signal_rows_by_session = {session_id: db.get_signals(session_id) for session_id in session_ids}

        messages = calculator._load_messages(session_ids, signal_rows_by_session)
        sessions = records_frame(sessions_rows, [
            "id", "username", "project_id", "datetime_start", "datetime_end", "start_state", "end_state"
        ])
        signals = cls._load_run_signals(db, run['id'], session_ids, signal_rows_by_session)
        transitions = signals.loc[signals["new_state"].notna()].copy() if not signals.empty else cls._empty_signals()

        return BenchmarkData(messages=messages, sessions=sessions, signals=signals, transitions=transitions)

    @classmethod
    def _load_run_signals(
        cls, db: Db, run_id: int, session_ids: list[int], signal_rows_by_session: dict[int, list[dict[str, Any]]],
    ) -> pd.DataFrame:
        rows = db.get_test_observations(run_id, session_ids)
        if not rows:
            return cls._empty_signals()

        annotations = [
            {
                'id': row['id'], 'message_id': row['message_id'], 'timestamp': row['timestamp'],
                'expected_values': row['expected_values'], 'session_id': session_id,
            }
            for session_id, session_rows in signal_rows_by_session.items()
            for row in session_rows
            if row['message_id'] is not None and row['expected_values']
        ]
        frame = pd.DataFrame.from_records(rows + annotations)
        for column in cls._SIGNALS_COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
        return frame[cls._SIGNALS_COLUMNS].sort_values(["session_id", "id"], kind="stable")

    @classmethod
    def _empty_signals(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=cls._SIGNALS_COLUMNS)
