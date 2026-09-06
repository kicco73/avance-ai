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

pytestmark = pytest.mark.regression


def _sessions(*days: tuple[int, int, int]) -> list[dict]:
    return [session_row(i + 1, datetime(*day), datetime(*day)) for i, day in enumerate(days)]


class TestEngagementMetric:
    def test_scores_zero_without_user_messages_and_low_on_low_activity(self):
        assert EngagementMetric().calculate(analytics_data()).value == 0.0

        assistant_only = [message_row(1, "assistant", datetime(2026, 1, 1)), message_row(2, "assistant", datetime(2026, 1, 1))]
        assert EngagementMetric().calculate(analytics_data(messages=assistant_only)).value == 0.0

        data = analytics_data(messages=[message_row(1, "user", datetime(2026, 1, 1))], sessions=_sessions((2026, 1, 1)))
        assert 0.0 < EngagementMetric(message_reference=100, session_reference=10).calculate(data).value < 20.0

    def test_value_is_the_weighted_split_of_its_components_saturating_at_100(self):
        messages = [message_row(i, "user", datetime(2026, 1, 1)) for i in range(50)]
        data = analytics_data(messages=messages, sessions=_sessions((2026, 1, 1)))
        result = EngagementMetric(message_reference=100, session_reference=10).calculate(data)
        assert result.components["user_messages"] == pytest.approx(50.0)
        assert result.components["sessions"] == pytest.approx(10.0)
        assert result.value == pytest.approx(50.0 * 0.6 + 10.0 * 0.4)

        messages = [message_row(i, "user", datetime(2026, 1, 1) + timedelta(minutes=i)) for i in range(150)]
        sessions = [session_row(i, datetime(2026, 1, 1), datetime(2026, 1, 1)) for i in range(15)]
        data = analytics_data(messages=messages, sessions=sessions)
        assert EngagementMetric(message_reference=100, session_reference=10).calculate(data).value == 100.0


class TestRetentionMetric:
    def test_fewer_than_two_sessions_scores_zero_and_gaps_within_the_horizon_score_100_regardless_of_input_order(self):
        assert RetentionMetric().calculate(analytics_data()).value == 0.0
        assert RetentionMetric().calculate(analytics_data(sessions=_sessions((2026, 1, 1)))).value == 0.0

        within = analytics_data(sessions=_sessions((2026, 1, 1), (2026, 1, 3), (2026, 1, 5)))
        assert RetentionMetric(horizon_days=14).calculate(within).value == 100.0

        unordered = analytics_data(sessions=_sessions((2026, 1, 3), (2026, 1, 1)))
        assert RetentionMetric(horizon_days=14).calculate(unordered).value == 100.0

    def test_gaps_beyond_the_horizon_score_zero_and_mixed_gaps_the_partial_return_rate(self):
        beyond = analytics_data(sessions=_sessions((2026, 1, 1), (2026, 3, 1)))
        assert RetentionMetric(horizon_days=14).calculate(beyond).value == 0.0

        mixed = analytics_data(sessions=_sessions((2026, 1, 1), (2026, 1, 3), (2026, 6, 1)))
        assert RetentionMetric(horizon_days=14).calculate(mixed).value == pytest.approx(50.0)


class TestActivityConsistencyMetric:
    def test_scores_zero_without_user_messages_or_with_a_single_active_day(self):
        assert ActivityConsistencyMetric().calculate(analytics_data()).value == 0.0

        assistant_only = [message_row(1, "assistant", datetime(2026, 1, 1)), message_row(2, "assistant", datetime(2026, 1, 2))]
        assert ActivityConsistencyMetric().calculate(analytics_data(messages=assistant_only)).value == 0.0

        single_day = [message_row(i, "user", datetime(2026, 1, 1, 9 + i)) for i in range(3)]
        result = ActivityConsistencyMetric().calculate(analytics_data(messages=single_day))
        assert result.value == 0.0
        assert result.components["coefficient_of_variation"] == 100.0

    def test_consistent_daily_volume_scores_100_and_bursty_volume_scores_low(self):
        consistent = [
            message_row(day * 5 + hour, "user", datetime(2026, 1, 1) + timedelta(days=day, hours=hour))
            for day in range(7) for hour in range(5)
        ]
        assert ActivityConsistencyMetric().calculate(analytics_data(messages=consistent)).value == 100.0

        bursty = [message_row(day, "user", datetime(2026, 1, 1) + timedelta(days=day)) for day in range(6)]
        bursty += [message_row(6 + minute, "user", datetime(2026, 1, 10) + timedelta(minutes=minute)) for minute in range(30)]
        assert ActivityConsistencyMetric().calculate(analytics_data(messages=bursty)).value < 50.0


class TestStateStabilityMetric:
    def test_only_real_state_changes_count_and_none_reads_as_fully_stable_even_without_sessions(self):
        assert StateStabilityMetric().calculate(analytics_data()).value == 100.0

        self_loop = [signal_row(1, datetime(2026, 1, 1), old_state="a", action="noop", new_state="a")]
        result = StateStabilityMetric().calculate(analytics_data(transitions=self_loop))
        assert result.value == 100.0
        assert result.components["real_transitions"] == 0.0

        mixed = [
            signal_row(1, datetime(2026, 1, 1), old_state="a", action="advance", new_state="b"),
            signal_row(2, datetime(2026, 1, 2), old_state="b", action="noop", new_state="b"),
        ]
        result = StateStabilityMetric().calculate(analytics_data(transitions=mixed))
        assert result.components["real_transitions"] == 1.0
        assert 0.0 <= result.value <= 100.0

    def test_a_long_stable_state_scores_high_while_frequent_or_repeated_short_states_score_zero(self):
        month = [session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 31))]
        one_transition = [signal_row(1, datetime(2026, 1, 2), old_state="a", action="advance", new_state="b")]
        result = StateStabilityMetric(reference_transitions_per_30_days=4.0).calculate(
            analytics_data(sessions=month, transitions=one_transition)
        )
        assert result.value > 70.0

        daily = [
            signal_row(i, datetime(2026, 1, 1) + timedelta(days=i), old_state=f"s{i}", action="advance", new_state=f"s{i + 1}")
            for i in range(20)
        ]
        result = StateStabilityMetric(reference_transitions_per_30_days=4.0).calculate(
            analytics_data(sessions=month, transitions=daily)
        )
        assert result.value == 0.0

        two_hours = [session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1, 2))]
        every_five_minutes = [
            signal_row(i, datetime(2026, 1, 1) + timedelta(minutes=i * 5), old_state=f"s{i}", action="advance", new_state=f"s{i + 1}")
            for i in range(10)
        ]
        assert StateStabilityMetric().calculate(analytics_data(sessions=two_hours, transitions=every_five_minutes)).value == 0.0


class TestSignalStabilityMetric:
    def test_needs_two_numeric_observations_per_signal_parsing_dict_values_like_json_strings(self):
        assert SignalStabilityMetric().calculate(analytics_data()).value == 0.0
        assert SignalStabilityMetric().calculate(analytics_data(signals=[signal_row(1, datetime(2026, 1, 1), values={"x": 50})])).value == 0.0

        typed = [
            signal_row(1, datetime(2026, 1, 1), values={"x": 40, "flag": True, "label": "hi"}),
            signal_row(2, datetime(2026, 1, 2), values={"x": 42, "flag": False, "label": "bye"}),
        ]
        assert set(SignalStabilityMetric().calculate(analytics_data(signals=typed)).components) == {"x"}

        as_dicts = [signal_row(1, datetime(2026, 1, 1)), signal_row(2, datetime(2026, 1, 2))]
        as_dicts[0]["values"] = {"x": 40}
        as_dicts[1]["values"] = {"x": 42}
        assert "x" in SignalStabilityMetric().calculate(analytics_data(signals=as_dicts)).components

    def test_stable_signals_score_high_volatile_ones_low_and_several_are_averaged(self):
        stable = [signal_row(i + 1, datetime(2026, 1, 1 + i), values={"x": v}) for i, v in enumerate((40, 42, 41, 43))]
        assert SignalStabilityMetric(change_reference=25.0).calculate(analytics_data(signals=stable)).value > 85.0

        volatile = [signal_row(i + 1, datetime(2026, 1, 1 + i), values={"x": v}) for i, v in enumerate((10, 90, 20, 85))]
        assert SignalStabilityMetric(change_reference=25.0).calculate(analytics_data(signals=volatile)).value < 15.0

        two = [
            signal_row(1, datetime(2026, 1, 1), values={"x": 40, "y": 10}),
            signal_row(2, datetime(2026, 1, 2), values={"x": 42, "y": 90}),
        ]
        result = SignalStabilityMetric(change_reference=25.0).calculate(analytics_data(signals=two))
        assert set(result.components) == {"x", "y"}
        assert result.value == pytest.approx((result.components["x"] + result.components["y"]) / 2)
