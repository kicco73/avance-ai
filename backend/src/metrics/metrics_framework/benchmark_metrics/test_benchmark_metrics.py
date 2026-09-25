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
        self.assertEqual(result.distribution[8], 1)
        self.assertEqual(result.distribution[9], 1)

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

    @staticmethod
    def _turns(
        roles: list[str], expected_states: dict[int, str], evaluations: list[dict], annotations: list[dict],
        tracked_signals_by_state: dict[str, frozenset[str]] | None = None,
    ) -> BenchmarkData:
        messages = pd.DataFrame([
            {
                "id": index + 1, "session_id": 1, "role": role,
                "timestamp": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=index),
                "expected_state": expected_states.get(index + 1),
            }
            for index, role in enumerate(roles)
        ])
        rows = [
            {
                "id": 100 + index, "session_id": 1, "message_id": row["message_id"],
                "timestamp": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=row["message_id"] - 1),
                "values": row.get("values"), "expected_values": None,
                "old_state": row.get("old_state"), "action": row.get("action"), "new_state": row.get("new_state"),
            }
            for index, row in enumerate(evaluations)
        ] + [
            {
                "id": 200 + index, "session_id": 1, "message_id": row["message_id"],
                "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
                "values": None, "expected_values": row["expected_values"],
                "old_state": None, "action": None, "new_state": None,
            }
            for index, row in enumerate(annotations)
        ]
        signals = pd.DataFrame(rows)
        sessions = pd.DataFrame([{
            "id": 1, "username": "u", "project_id": "p", "datetime_start": pd.Timestamp("2026-01-01T00:00:00Z"),
            "datetime_end": None, "start_state": "a", "end_state": None,
        }])
        return BenchmarkData(
            messages=messages, sessions=sessions, signals=signals,
            transitions=signals.loc[signals["new_state"].notna()].copy(),
            tracked_signals_by_state=tracked_signals_by_state,
        )

    @staticmethod
    def _observations_of(data: BenchmarkData) -> tuple[BenchmarkObservation, ...]:
        return BenchmarkObservationBuilder(BenchmarkConfiguration()).build(data)

    def test_an_annotation_on_the_user_message_is_compared_with_the_evaluation_after_the_ai_reply(self) -> None:
        data = self._turns(
            ["user", "assistant"],
            expected_states={1: "b"},
            evaluations=[{"message_id": 2, "values": '{"score": 60}', "old_state": "a", "action": "go", "new_state": "b"}],
            annotations=[{"message_id": 1, "expected_values": '{"score": 80}'}],
        )
        observations = self._observations_of(data)

        signal = SignalAccuracyMetric().calculate(observations)
        state = StateAccuracyMetric().calculate(observations)
        self.assertEqual((signal.value, signal.sample_count), (80.0, 1))
        self.assertEqual((state.value, state.sample_count), (100.0, 1))

    def test_an_annotation_on_the_ai_reply_is_compared_with_the_next_evaluation_after_the_user_message(self) -> None:
        data = self._turns(
            ["user", "assistant", "user"],
            expected_states={},
            evaluations=[
                {"message_id": 1, "values": '{"score": 10}', "new_state": "a"},
                {"message_id": 3, "values": '{"score": 70}', "new_state": "a"},
            ],
            annotations=[{"message_id": 2, "expected_values": '{"score": 80}'}],
        )
        result = SignalAccuracyMetric().calculate(self._observations_of(data))

        self.assertEqual((result.value, result.sample_count), (90.0, 1))

    def test_an_annotation_with_no_evaluation_after_it_is_not_compared(self) -> None:
        data = self._turns(
            ["user", "assistant"],
            expected_states={2: "a"},
            evaluations=[{"message_id": 1, "values": '{"score": 80}', "new_state": "a"}],
            annotations=[{"message_id": 2, "expected_values": '{"score": 80}'}],
        )
        observations = self._observations_of(data)

        self.assertEqual(SignalAccuracyMetric().calculate(observations).sample_count, 0)
        self.assertEqual(StateAccuracyMetric().calculate(observations).sample_count, 0)

    def test_two_annotations_reaching_the_same_evaluation_are_one_comparison_with_the_later_one(self) -> None:
        data = self._turns(
            ["user", "assistant"],
            expected_states={},
            evaluations=[{"message_id": 2, "values": '{"score": 80}', "new_state": "a"}],
            annotations=[
                {"message_id": 1, "expected_values": '{"score": 50}'},
                {"message_id": 2, "expected_values": '{"score": 80}'},
            ],
        )
        result = SignalAccuracyMetric().calculate(self._observations_of(data))

        self.assertEqual((result.value, result.sample_count), (100.0, 1))

    def _two_turns_scored_in_state_a(self, tracked: frozenset[str], second_value: str = '{"score": 80}') -> BenchmarkData:
        return self._turns(
            ["user", "assistant", "user", "assistant"],
            expected_states={},
            evaluations=[
                {"message_id": 1, "values": '{"score": 20}', "new_state": "a"},
                {"message_id": 3, "values": second_value, "new_state": "a"},
            ],
            annotations=[
                {"message_id": 1, "expected_values": '{"score": 80}'},
                {"message_id": 3, "expected_values": '{"score": 80}'},
            ],
            tracked_signals_by_state={"a": tracked},
        )

    def test_a_signal_the_replays_state_does_not_track_is_not_scored(self) -> None:
        observations = self._observations_of(self._two_turns_scored_in_state_a(frozenset({"other"})))

        assert all(observation.signal_agreements == {} for observation in observations)
        self.assertEqual(SignalAccuracyMetric().calculate(observations).sample_count, 0)

    def test_a_tracked_signal_without_a_value_still_scores_zero(self) -> None:
        observations = self._observations_of(self._two_turns_scored_in_state_a(frozenset({"score"}), second_value="{}"))

        result = SignalAccuracyMetric().calculate(observations)
        self.assertEqual((result.value, result.sample_count), (20.0, 2))

    def test_a_tracked_signal_with_a_value_is_scored_as_before(self) -> None:
        observations = self._observations_of(self._two_turns_scored_in_state_a(frozenset({"score"})))

        result = SignalAccuracyMetric().calculate(observations)
        self.assertEqual((result.value, result.sample_count), (70.0, 2))

    def test_stability_and_consistency_ignore_an_untracked_signal(self) -> None:
        tracked = self._observations_of(self._two_turns_scored_in_state_a(frozenset({"score"})))
        untracked = self._observations_of(self._two_turns_scored_in_state_a(frozenset({"other"})))

        self.assertLess(BenchmarkStabilityMetric().calculate(tracked).components["signal_error"], 100.0)
        self.assertEqual(BenchmarkStabilityMetric().calculate(untracked).components["signal_error"], 100.0)
        self.assertIn("score", BenchmarkConsistencyMetric(BenchmarkConfiguration()).calculate(tracked).components)
        self.assertNotIn("score", BenchmarkConsistencyMetric(BenchmarkConfiguration()).calculate(untracked).components)

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
