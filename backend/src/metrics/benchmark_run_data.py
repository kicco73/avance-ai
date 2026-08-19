"""Builds the hybrid BenchmarkData a BenchmarkRun's own metrics are
calculated from (see BenchmarkCalculator.from_data): messages/sessions
stay real (they carry expected_state, ground truth, never touched by a
replay) — only signals/transitions come from BenchmarkRunObservation
instead of Tracking, with expected_values still injected from the real
Tracking row for the same message_id (a join, never a copy)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from db import Db
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkData

_SIGNALS_COLUMNS = ["id", "message_id", "timestamp", "values", "expected_values", "old_state", "action", "new_state", "session_id"]


def build_benchmark_run_data(db: Db, run: dict) -> BenchmarkData:
    calculator = BenchmarkCalculator(db, run['username'], run['project_name'], session_id=run['session_id'])

    sessions_rows = calculator._load_sessions()
    session_ids = [int(row['id']) for row in sessions_rows]
    # Same real Tracking rows _load_messages/_load_signals already use —
    # loaded once, reused both for messages' own expected_state and, here,
    # for expected_values injected into the replayed signals below.
    signal_rows_by_session = {session_id: db.get_signals(session_id) for session_id in session_ids}

    messages = calculator._load_messages(session_ids, signal_rows_by_session)
    sessions = calculator._frame(sessions_rows, [
        "id", "username", "project_name", "datetime_start", "datetime_end", "start_state", "end_state"
    ])
    signals = _load_run_signals(db, run['id'], session_ids, signal_rows_by_session)
    transitions = signals.loc[signals["new_state"].notna()].copy() if not signals.empty else _empty_signals()

    return BenchmarkData(messages=messages, sessions=sessions, signals=signals, transitions=transitions)


def _load_run_signals(
    db: Db, run_id: int, session_ids: list[int], signal_rows_by_session: dict[int, list[dict[str, Any]]],
) -> pd.DataFrame:
    expected_values_by_message = {
        row['message_id']: row['expected_values']
        for rows in signal_rows_by_session.values()
        for row in rows
        if row['message_id'] is not None
    }

    rows = db.get_benchmark_run_observations(run_id, session_ids)
    if not rows:
        return _empty_signals()

    records = [{**row, 'expected_values': expected_values_by_message.get(row['message_id'])} for row in rows]
    frame = pd.DataFrame.from_records(records)
    for column in _SIGNALS_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame[_SIGNALS_COLUMNS].sort_values(["session_id", "id"], kind="stable")


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=_SIGNALS_COLUMNS)
