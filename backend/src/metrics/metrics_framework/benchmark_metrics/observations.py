from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from .dto import BenchmarkConfiguration, BenchmarkObservation
from .normalization import BenchmarkNormalizer


@dataclass(frozen=True)
class BenchmarkData(object):
    messages: pd.DataFrame
    sessions: pd.DataFrame
    signals: pd.DataFrame
    transitions: pd.DataFrame


class BenchmarkObservationBuilder(object):
    """Builds atomic expert-vs-system observations from analytical frames."""

    def __init__(self, configuration: BenchmarkConfiguration) -> None:
        self._configuration = configuration

    def build(self, data: BenchmarkData) -> tuple[BenchmarkObservation, ...]:
        if data.messages.empty:
            return ()

        messages = data.messages.sort_values(["session_id", "id"], kind="stable")
        signals = data.signals.sort_values(["session_id", "id"], kind="stable")
        transitions = data.transitions.sort_values(["session_id", "id"], kind="stable")
        sessions = data.sessions.set_index("id") if not data.sessions.empty else pd.DataFrame()

        observations: list[BenchmarkObservation] = []
        for row in self._points(messages, signals):
            session_id = int(row["session_id"])
            point_timestamp = pd.Timestamp(row["timestamp"])
            message_id = int(row["message_id"])
            session_messages = messages.loc[messages["session_id"].eq(session_id)].copy()
            session_messages = session_messages.sort_values(["id"], kind="stable")
            expected_state = row.get("expected_state")
            expected_values = self._parse_values(row.get("expected_values"))
            actual_state = self._state_after_message(session_id, message_id, transitions, sessions)

            state_agreement: float | None = None
            if expected_state:
                state_agreement = 100.0 if actual_state == expected_state else 0.0

            signal_agreements: dict[str, float] = {}
            signal_errors: dict[str, float] = {}
            signal_signed_errors: dict[str, float] = {}
            actual_values = self._parse_values(row.get("actual_values"))
            for name, expected in expected_values.items():
                if not isinstance(expected, (int, float)) or isinstance(expected, bool):
                    continue
                actual = actual_values.get(name)
                agreement = BenchmarkNormalizer.signal_agreement(
                    float(actual) if isinstance(actual, (int, float)) and not isinstance(actual, bool) else None,
                    float(expected),
                )
                signal_agreements[name] = agreement
                signal_errors[name] = 100.0 - agreement
                if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                    signal_signed_errors[name] = float(actual) - float(expected)

            expected_transition = self._is_expected_transition(
                message_id=message_id,
                expected_state=expected_state,
                session_messages=session_messages,
                sessions=sessions,
            )
            message_delay: int | None = None
            time_delay_seconds: float | None = None
            responsiveness: float | None = None

            if expected_transition and expected_state:
                transition = self._transition_for_expected_point(
                    session_id, message_id, expected_state, actual_state, transitions
                )
                expected_position = self._message_position(session_messages, message_id)
                max_seconds = self._configuration.max_session_duration_in_minutes * 60.0
                if transition is not None:
                    actual_position = self._message_position(session_messages, transition["message_id"])
                    if expected_position is not None and actual_position is not None:
                        message_delay = actual_position - expected_position
                    transition_timestamp = pd.Timestamp(transition["timestamp"])
                    if pd.notna(point_timestamp) and pd.notna(transition_timestamp):
                        time_delay_seconds = float(
                            (transition_timestamp - point_timestamp).total_seconds()
                        )
                    message_quality = self._message_delay_quality(
                        message_delay,
                        session_messages,
                        expected_position,
                    )
                    time_quality = (
                        BenchmarkNormalizer.delay_to_quality(abs(time_delay_seconds), max_seconds)
                        if time_delay_seconds is not None else None
                    )
                    if message_quality is not None and time_quality is not None:
                        responsiveness = (message_quality + time_quality) / 2.0
                    elif message_quality is not None:
                        responsiveness = message_quality
                    elif time_quality is not None:
                        responsiveness = time_quality
                else:
                    # The expert expected the transition, but the system never
                    # reached the expected state during the session. This is a
                    # real benchmark failure, not a missing sample.
                    responsiveness = 0.0

            observations.append(
                BenchmarkObservation(
                    session_id=session_id,
                    message_id=message_id,
                    expected_state=expected_state or None,
                    actual_state=actual_state,
                    state_agreement=state_agreement,
                    signal_agreements=signal_agreements,
                    signal_errors=signal_errors,
                signal_signed_errors=signal_signed_errors,
                    expected_transition=expected_transition,
                    message_delay=message_delay,
                    time_delay_seconds=time_delay_seconds,
                    transition_responsiveness=responsiveness,
                )
            )
        return tuple(observations)

    @staticmethod
    def _parse_values(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _points(messages: pd.DataFrame, signals: pd.DataFrame) -> list[dict[str, Any]]:
        """Return annotated evaluation points, associating signal annotations
        with the message whose post-message evaluation produced the signal row.
        """
        points: dict[tuple[int, int], dict[str, Any]] = {}
        for row in messages.itertuples(index=False):
            # Missing values come back from pandas as float NaN, not None/''/
            # falsy — plain truthiness treats NaN as truthy and would turn
            # every unannotated message into a spurious expected_state point.
            if pd.notna(row.expected_state) and row.expected_state:
                points[(int(row.session_id), int(row.id))] = {
                    "session_id": int(row.session_id),
                    "message_id": int(row.id),
                    "timestamp": pd.Timestamp(row.timestamp),
                    "expected_state": str(row.expected_state),
                    "expected_values": None,
                    "actual_values": None,
                }

        if not signals.empty:
            for row in signals.itertuples(index=False):
                expected_values = row.expected_values
                if pd.isna(expected_values) or not expected_values:
                    continue
                if pd.isna(row.message_id):
                    raise ValueError(
                        f"Tracking row {row.id!r} in session {row.session_id!r} has expected_values "
                        "but no message_id."
                    )
                message_rows = messages.loc[messages["id"].eq(int(row.message_id))]
                if message_rows.empty:
                    raise ValueError(
                        f"Tracking row {row.id!r} in session {row.session_id!r} references unknown "
                        f"message_id {int(row.message_id)!r}."
                    )
                message = message_rows.iloc[0]
                key = (int(row.session_id), int(message["id"]))
                message_expected_state = message.get("expected_state")
                point = points.setdefault(
                    key,
                    {
                        "session_id": int(row.session_id),
                        "message_id": int(message["id"]),
                        "timestamp": pd.Timestamp(row.timestamp),
                        "expected_state": message_expected_state if pd.notna(message_expected_state) else None,
                        "expected_values": None,
                        "actual_values": None,
                    },
                )
                point["expected_values"] = expected_values
                point["actual_values"] = row.values
                point["timestamp"] = pd.Timestamp(row.timestamp)

        return sorted(points.values(), key=lambda point: (point["session_id"], point["message_id"]))

    @staticmethod
    def _state_after_message(
        session_id: int,
        message_id: int,
        transitions: pd.DataFrame,
        sessions: pd.DataFrame,
    ) -> str | None:
        rows = transitions.loc[
            transitions["session_id"].eq(session_id) & (transitions["message_id"] <= message_id)
        ]
        if not rows.empty:
            value = rows.iloc[-1]["new_state"]
            return str(value) if pd.notna(value) else None
        if session_id in sessions.index:
            value = sessions.loc[session_id, "start_state"]
            return str(value) if pd.notna(value) else None
        return None

    @staticmethod
    def _message_position(messages: pd.DataFrame, message_id: int) -> int | None:
        rows = messages.index[messages["id"].eq(message_id)]
        if len(rows) == 0:
            return None
        return int(rows[0]) - int(messages.index[0])

    @staticmethod
    def _transition_for_expected_point(
        session_id: int,
        message_id: int,
        expected_state: str,
        actual_state: str | None,
        transitions: pd.DataFrame,
    ) -> dict[str, Any] | None:
        rows = transitions.loc[
            transitions["session_id"].eq(session_id)
            & transitions["new_state"].eq(expected_state)
        ].copy()
        if rows.empty:
            return None
        if actual_state == expected_state:
            prior = rows.loc[rows["message_id"] <= message_id]
            if prior.empty:
                return None
            return prior.sort_values(["id"], kind="stable").iloc[-1].to_dict()
        future = rows.loc[rows["message_id"] > message_id]
        if future.empty:
            return None
        return future.sort_values(["id"], kind="stable").iloc[0].to_dict()

    @staticmethod
    def _is_expected_transition(
        message_id: int,
        expected_state: object,
        session_messages: pd.DataFrame,
        sessions: pd.DataFrame,
    ) -> bool:
        if not expected_state:
            return False
        earlier = session_messages.loc[
            (session_messages["id"] < message_id)
            & session_messages["expected_state"].notna()
            & session_messages["expected_state"].ne("")
        ]
        if not earlier.empty:
            previous = earlier.sort_values(["id"], kind="stable").iloc[-1]["expected_state"]
            return str(previous) != str(expected_state)
        session_id = int(session_messages.iloc[0]["session_id"])
        if session_id in sessions.index:
            start_state = sessions.loc[session_id, "start_state"]
            return pd.isna(start_state) or str(start_state) != str(expected_state)
        return True

    @staticmethod
    def _message_delay_quality(
        message_delay: int | None,
        session_messages: pd.DataFrame,
        expected_position: int | None,
    ) -> float | None:
        if message_delay is None or expected_position is None:
            return None
        # The largest observable positive delay is the remaining number of
        # messages in the session; negative delays are penalized symmetrically.
        max_positive = max(1, len(session_messages) - 1 - expected_position)
        max_negative = max(1, expected_position)
        max_delay = max_positive if message_delay >= 0 else max_negative
        return BenchmarkNormalizer.delay_to_quality(abs(message_delay), float(max_delay))
