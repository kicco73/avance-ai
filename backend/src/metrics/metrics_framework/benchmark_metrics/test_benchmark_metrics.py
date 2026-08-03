from __future__ import annotations

import unittest

import pandas as pd

from .dto import BenchmarkConfiguration
from .metrics import (
    BenchmarkAccuracyMetric,
    BenchmarkConsistencyMetric,
    BenchmarkStabilityMetric,
    SignalAccuracyMetric,
    StateAccuracyMetric,
    TransitionResponsivenessMetric,
)
from .observations import BenchmarkData, BenchmarkObservationBuilder


class BenchmarkMetricsTest(unittest.TestCase):
    def _data(self) -> BenchmarkData:
        messages = pd.DataFrame(
            [
                {"id": 1, "session_id": 1, "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"), "expected_state": "a"},
                {"id": 2, "session_id": 1, "timestamp": pd.Timestamp("2026-01-01T00:10:00Z"), "expected_state": "b"},
                {"id": 3, "session_id": 1, "timestamp": pd.Timestamp("2026-01-01T00:20:00Z"), "expected_state": None},
            ]
        )
        signals = pd.DataFrame(
            [
                {
                    "id": 10,
                    "session_id": 1,
                    "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
                    "values": '{"score": 80}',
                    "expected_values": '{"score": 80}',
                    "old_state": None,
                    "action": None,
                    "new_state": "a",
                },
                {
                    "id": 11,
                    "session_id": 1,
                    "timestamp": pd.Timestamp("2026-01-01T00:12:00Z"),
                    "values": '{"score": 60}',
                    "expected_values": '{"score": 80}',
                    "old_state": "a",
                    "action": "go",
                    "new_state": "b",
                },
            ]
        )
        sessions = pd.DataFrame(
            [{
                "id": 1,
                "username": "u",
                "project_name": "p",
                "datetime_start": pd.Timestamp("2026-01-01T00:00:00Z"),
                "datetime_end": pd.Timestamp("2026-01-01T00:20:00Z"),
                "start_state": "a",
                "end_state": "b",
            }]
        )
        transitions = signals.loc[signals["new_state"].notna()].copy()
        return BenchmarkData(messages=messages, sessions=sessions, signals=signals, transitions=transitions)

    def test_perfect_state_accuracy(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        result = StateAccuracyMetric().calculate(observations)
        self.assertEqual(result.value, 100.0)

    def test_signal_accuracy(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        result = SignalAccuracyMetric().calculate(observations)
        self.assertEqual(result.value, 90.0)

    def test_transition_responsiveness_is_normalized(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        result = TransitionResponsivenessMetric().calculate(observations)
        self.assertGreaterEqual(result.value, 0.0)
        self.assertLessEqual(result.value, 100.0)

    def test_all_results_are_normalized(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        metrics = (
            StateAccuracyMetric(),
            SignalAccuracyMetric(),
            TransitionResponsivenessMetric(),
            BenchmarkAccuracyMetric(),
            BenchmarkStabilityMetric(),
            BenchmarkConsistencyMetric(BenchmarkConfiguration()),
        )
        for metric in metrics:
            result = metric.calculate(observations)
            self.assertGreaterEqual(result.value, 0.0)
            self.assertLessEqual(result.value, 100.0)


if __name__ == "__main__":
    unittest.main()
