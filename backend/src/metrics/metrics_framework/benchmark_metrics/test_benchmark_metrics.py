from __future__ import annotations

import unittest

import pandas as pd

from .dto import BenchmarkConfiguration, BenchmarkObservation
from .metrics import (
    BenchmarkAccuracyMetric,
    BenchmarkConsistencyMetric,
    BenchmarkStabilityMetric,
    SignalAccuracyMetric,
    StateAccuracyMetric,
    StateAccuracyStableMetric,
    StateAccuracyTransitionMetric,
    Statistics,
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
                    "message_id": 1,
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
                    "message_id": 2,
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
                "project_id": "p",
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

    def test_signal_accuracy_distribution_buckets_each_observation(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        result = SignalAccuracyMetric().calculate(observations)
        self.assertEqual(len(result.distribution), Statistics.DISTRIBUTION_BUCKET_COUNT)
        self.assertEqual(sum(result.distribution), result.sample_count)
        self.assertEqual(result.distribution[8], 1)  # the 80% observation
        self.assertEqual(result.distribution[9], 1)  # the 100% observation

    def test_distribution_is_empty_with_no_values(self) -> None:
        result = Statistics.result("empty", [])
        self.assertEqual(result.distribution, ())

    def test_distribution_boundary_value_lands_in_the_last_bucket(self) -> None:
        result = Statistics.result("boundary", [0.0, 100.0])
        self.assertEqual(result.distribution[0], 1)
        self.assertEqual(result.distribution[-1], 1)
        self.assertEqual(sum(result.distribution), 2)

    def test_transition_responsiveness_is_normalized(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        result = TransitionResponsivenessMetric().calculate(observations)
        self.assertGreaterEqual(result.value, 0.0)
        self.assertLessEqual(result.value, 100.0)

    @staticmethod
    def _observation(*, expected_transition: bool, state_agreement: float | None) -> BenchmarkObservation:
        return BenchmarkObservation(
            session_id=1,
            message_id=1,
            expected_state="a",
            actual_state="a",
            state_agreement=state_agreement,
            expected_transition=expected_transition,
        )

    def test_state_accuracy_stable_uses_only_non_transition_points(self) -> None:
        observations = (
            self._observation(expected_transition=False, state_agreement=100.0),
            self._observation(expected_transition=False, state_agreement=0.0),
        )
        result = StateAccuracyStableMetric().calculate(observations)
        self.assertEqual(result.value, 50.0)
        self.assertEqual(result.sample_count, 2)

    def test_state_accuracy_transition_uses_only_transition_points(self) -> None:
        observations = (
            self._observation(expected_transition=True, state_agreement=100.0),
            self._observation(expected_transition=True, state_agreement=100.0),
        )
        result = StateAccuracyTransitionMetric().calculate(observations)
        self.assertEqual(result.value, 100.0)
        self.assertEqual(result.sample_count, 2)

    def test_state_accuracy_stable_and_transition_split_a_mixed_set(self) -> None:
        observations = (
            self._observation(expected_transition=False, state_agreement=100.0),
            self._observation(expected_transition=False, state_agreement=100.0),
            self._observation(expected_transition=True, state_agreement=0.0),
        )
        stable = StateAccuracyStableMetric().calculate(observations)
        transition = StateAccuracyTransitionMetric().calculate(observations)
        self.assertEqual(stable.value, 100.0)
        self.assertEqual(stable.sample_count, 2)
        self.assertEqual(transition.value, 0.0)
        self.assertEqual(transition.sample_count, 1)
        # state_accuracy itself stays unaffected by the split — still
        # every point regardless of expected_transition.
        overall = StateAccuracyMetric().calculate(observations)
        self.assertAlmostEqual(overall.value, 200.0 / 3.0)
        self.assertEqual(overall.sample_count, 3)

    def test_state_accuracy_stable_and_transition_are_empty_with_no_observations(self) -> None:
        stable = StateAccuracyStableMetric().calculate(())
        transition = StateAccuracyTransitionMetric().calculate(())
        self.assertEqual(stable.value, 0.0)
        self.assertEqual(stable.sample_count, 0)
        self.assertEqual(transition.value, 0.0)
        self.assertEqual(transition.sample_count, 0)

    def test_all_results_are_normalized(self) -> None:
        observations = BenchmarkObservationBuilder(BenchmarkConfiguration()).build(self._data())
        metrics = (
            StateAccuracyMetric(),
            StateAccuracyStableMetric(),
            StateAccuracyTransitionMetric(),
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
