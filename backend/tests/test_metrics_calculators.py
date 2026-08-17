from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from metrics.metrics_framework.metrics import (
    ActivityConsistencyMetric,
    EngagementMetric,
    RetentionMetric,
    SignalStabilityMetric,
    StateStabilityMetric,
)

from metrics_helpers import analytics_data, message_row, session_row, signal_row

# Every test in this file exercises one metric's calculate() against a
# fixed, hand-built dataset and asserts a specific numeric/component
# outcome — punctual facts about behavior, not a shape/schema/ordering
# guarantee — so uniformly regression.
pytestmark = pytest.mark.regression


class TestEngagementMetric:
    def test_empty_history_scores_zero(self):
        result = EngagementMetric().calculate(analytics_data())
        assert result.value == 0.0

    def test_low_activity_scores_low(self):
        data = analytics_data(
            messages=[message_row(1, "user", datetime(2026, 1, 1))],
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
        )
        result = EngagementMetric(message_reference=100, session_reference=10).calculate(data)
        assert 0.0 < result.value < 20.0

    def test_high_activity_saturates_at_100(self):
        messages = [message_row(i, "user", datetime(2026, 1, 1) + timedelta(minutes=i)) for i in range(150)]
        sessions = [session_row(i, datetime(2026, 1, 1), datetime(2026, 1, 1)) for i in range(15)]
        data = analytics_data(messages=messages, sessions=sessions)
        result = EngagementMetric(message_reference=100, session_reference=10).calculate(data)
        assert result.value == 100.0

    def test_only_user_messages_count_toward_message_activity(self):
        messages = [message_row(1, "assistant", datetime(2026, 1, 1)), message_row(2, "assistant", datetime(2026, 1, 1))]
        result = EngagementMetric().calculate(analytics_data(messages=messages))
        assert result.value == 0.0

    def test_value_is_the_weighted_split_of_its_components(self):
        messages = [message_row(i, "user", datetime(2026, 1, 1)) for i in range(50)]
        sessions = [session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))]
        data = analytics_data(messages=messages, sessions=sessions)
        result = EngagementMetric(message_reference=100, session_reference=10).calculate(data)
        assert result.components["user_messages"] == pytest.approx(50.0)
        assert result.components["sessions"] == pytest.approx(10.0)
        assert result.value == pytest.approx(50.0 * 0.6 + 10.0 * 0.4)


class TestRetentionMetric:
    def test_fewer_than_two_sessions_scores_zero(self):
        data = analytics_data(sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))])
        assert RetentionMetric().calculate(data).value == 0.0

    def test_no_sessions_scores_zero(self):
        assert RetentionMetric().calculate(analytics_data()).value == 0.0

    def test_all_gaps_within_horizon_score_100(self):
        sessions = [
            session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
            session_row(2, datetime(2026, 1, 3), datetime(2026, 1, 3)),
            session_row(3, datetime(2026, 1, 5), datetime(2026, 1, 5)),
        ]
        result = RetentionMetric(horizon_days=14).calculate(analytics_data(sessions=sessions))
        assert result.value == 100.0

    def test_all_gaps_beyond_horizon_score_zero(self):
        sessions = [
            session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
            session_row(2, datetime(2026, 3, 1), datetime(2026, 3, 1)),
        ]
        result = RetentionMetric(horizon_days=14).calculate(analytics_data(sessions=sessions))
        assert result.value == 0.0

    def test_mixed_gaps_score_the_partial_return_rate(self):
        sessions = [
            session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
            session_row(2, datetime(2026, 1, 3), datetime(2026, 1, 3)),  # within horizon
            session_row(3, datetime(2026, 6, 1), datetime(2026, 6, 1)),  # outside horizon
        ]
        result = RetentionMetric(horizon_days=14).calculate(analytics_data(sessions=sessions))
        assert result.value == pytest.approx(50.0)

    def test_session_order_in_the_input_does_not_matter(self):
        sessions = [
            session_row(2, datetime(2026, 1, 3), datetime(2026, 1, 3)),
            session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
        ]
        result = RetentionMetric(horizon_days=14).calculate(analytics_data(sessions=sessions))
        assert result.value == 100.0


class TestActivityConsistencyMetric:
    def test_empty_history_scores_zero(self):
        assert ActivityConsistencyMetric().calculate(analytics_data()).value == 0.0

    def test_single_active_day_scores_zero(self):
        messages = [message_row(i, "user", datetime(2026, 1, 1, 9 + i)) for i in range(3)]
        result = ActivityConsistencyMetric().calculate(analytics_data(messages=messages))
        assert result.value == 0.0
        assert result.components["coefficient_of_variation"] == 100.0

    def test_consistent_daily_volume_scores_100(self):
        messages = []
        next_id = 0
        for day in range(7):
            for hour in range(5):
                messages.append(message_row(next_id, "user", datetime(2026, 1, 1) + timedelta(days=day, hours=hour)))
                next_id += 1
        result = ActivityConsistencyMetric().calculate(analytics_data(messages=messages))
        assert result.value == 100.0  # zero variance -> cv=0 -> 100/(1+0)

    def test_bursty_daily_volume_scores_low(self):
        messages = []
        next_id = 0
        for day in range(6):  # 6 quiet days, 1 message each
            messages.append(message_row(next_id, "user", datetime(2026, 1, 1) + timedelta(days=day)))
            next_id += 1
        for minute in range(30):  # then a single day with a burst of 30
            messages.append(message_row(next_id, "user", datetime(2026, 1, 10) + timedelta(minutes=minute)))
            next_id += 1
        result = ActivityConsistencyMetric().calculate(analytics_data(messages=messages))
        assert result.value < 50.0

    def test_only_user_messages_count(self):
        messages = [
            message_row(1, "assistant", datetime(2026, 1, 1)),
            message_row(2, "assistant", datetime(2026, 1, 2)),
        ]
        result = ActivityConsistencyMetric().calculate(analytics_data(messages=messages))
        assert result.value == 0.0


class TestStateStabilityMetric:
    def test_no_transitions_scores_100(self):
        assert StateStabilityMetric().calculate(analytics_data()).value == 100.0

    def test_self_loops_only_score_100(self):
        transitions = [signal_row(1, datetime(2026, 1, 1), old_state="a", action="noop", new_state="a")]
        result = StateStabilityMetric().calculate(analytics_data(transitions=transitions))
        assert result.value == 100.0
        assert result.components["real_transitions"] == 0.0

    def test_long_stable_state_scores_high(self):
        sessions = [session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 31))]
        transitions = [signal_row(1, datetime(2026, 1, 2), old_state="a", action="advance", new_state="b")]
        result = StateStabilityMetric(reference_transitions_per_30_days=4.0).calculate(
            analytics_data(sessions=sessions, transitions=transitions)
        )
        assert result.value > 70.0

    def test_frequent_transitions_score_low(self):
        sessions = [session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 31))]
        transitions = [
            signal_row(
                i, datetime(2026, 1, 1) + timedelta(days=i), old_state=f"s{i}", action="advance", new_state=f"s{i + 1}"
            )
            for i in range(20)
        ]
        result = StateStabilityMetric(reference_transitions_per_30_days=4.0).calculate(
            analytics_data(sessions=sessions, transitions=transitions)
        )
        assert result.value == 0.0

    def test_repeated_short_states_score_low(self):
        sessions = [session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1, 2))]
        transitions = [
            signal_row(
                i, datetime(2026, 1, 1) + timedelta(minutes=i * 5), old_state=f"s{i}", action="advance", new_state=f"s{i + 1}"
            )
            for i in range(10)
        ]
        result = StateStabilityMetric().calculate(analytics_data(sessions=sessions, transitions=transitions))
        assert result.value == 0.0

    def test_falls_back_to_transition_timestamps_without_sessions(self):
        transitions = [
            signal_row(1, datetime(2026, 1, 1), old_state="a", action="advance", new_state="b"),
            signal_row(2, datetime(2026, 1, 2), old_state="b", action="advance", new_state="c"),
        ]
        result = StateStabilityMetric().calculate(analytics_data(transitions=transitions))
        assert 0.0 <= result.value <= 100.0

    def test_transition_only_rows_without_a_real_state_change_are_excluded_from_the_count(self):
        transitions = [
            signal_row(1, datetime(2026, 1, 1), old_state="a", action="advance", new_state="b"),
            signal_row(2, datetime(2026, 1, 2), old_state="b", action="noop", new_state="b"),
        ]
        result = StateStabilityMetric().calculate(analytics_data(transitions=transitions))
        assert result.components["real_transitions"] == 1.0


class TestSignalStabilityMetric:
    def test_no_signals_scores_zero(self):
        assert SignalStabilityMetric().calculate(analytics_data()).value == 0.0

    def test_single_observation_is_ignored(self):
        signals = [signal_row(1, datetime(2026, 1, 1), values={"x": 50})]
        result = SignalStabilityMetric().calculate(analytics_data(signals=signals))
        assert result.value == 0.0

    def test_stable_signal_scores_high(self):
        signals = [
            signal_row(1, datetime(2026, 1, 1), values={"x": 40}),
            signal_row(2, datetime(2026, 1, 2), values={"x": 42}),
            signal_row(3, datetime(2026, 1, 3), values={"x": 41}),
            signal_row(4, datetime(2026, 1, 4), values={"x": 43}),
        ]
        result = SignalStabilityMetric(change_reference=25.0).calculate(analytics_data(signals=signals))
        assert result.value > 85.0

    def test_volatile_signal_scores_low(self):
        signals = [
            signal_row(1, datetime(2026, 1, 1), values={"x": 10}),
            signal_row(2, datetime(2026, 1, 2), values={"x": 90}),
            signal_row(3, datetime(2026, 1, 3), values={"x": 20}),
            signal_row(4, datetime(2026, 1, 4), values={"x": 85}),
        ]
        result = SignalStabilityMetric(change_reference=25.0).calculate(analytics_data(signals=signals))
        assert result.value < 15.0

    def test_multiple_signals_are_averaged(self):
        signals = [
            signal_row(1, datetime(2026, 1, 1), values={"x": 40, "y": 10}),
            signal_row(2, datetime(2026, 1, 2), values={"x": 42, "y": 90}),
        ]
        result = SignalStabilityMetric(change_reference=25.0).calculate(analytics_data(signals=signals))
        assert set(result.components) == {"x", "y"}
        assert result.value == pytest.approx((result.components["x"] + result.components["y"]) / 2)

    def test_non_numeric_and_boolean_values_are_ignored(self):
        signals = [
            signal_row(1, datetime(2026, 1, 1), values={"x": 40, "flag": True, "label": "hi"}),
            signal_row(2, datetime(2026, 1, 2), values={"x": 42, "flag": False, "label": "bye"}),
        ]
        result = SignalStabilityMetric().calculate(analytics_data(signals=signals))
        assert set(result.components) == {"x"}

    def test_values_stored_as_a_plain_dict_are_parsed_same_as_json_strings(self):
        # The real DB always stores `values` as a JSON string (see
        # Tracking.values), but the Timeline layer accepts either shape (see
        # Timeline._parse_values) — exercised directly here since
        # signal_row() always JSON-encodes.
        signals = [
            signal_row(1, datetime(2026, 1, 1)),
            signal_row(2, datetime(2026, 1, 2)),
        ]
        signals[0]["values"] = {"x": 40}
        signals[1]["values"] = {"x": 42}
        result = SignalStabilityMetric().calculate(analytics_data(signals=signals))
        assert "x" in result.components
