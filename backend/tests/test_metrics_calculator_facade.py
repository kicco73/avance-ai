from __future__ import annotations

import json
from datetime import datetime

import pytest

from metrics_framework import AnalyticsCalculator
from metrics_framework.timeline import Timeline, UserAnalyticsDataBuilder
from metrics_helpers import message_row, session_row, signal_row


class FakeAnalyticsDb:
    """Protocol-shaped fake (see metrics_framework.interfaces.AnalyticsDb) —
    exercises UserAnalyticsDataBuilder/AnalyticsCalculator exactly the way
    the real Db facade is consumed, without touching Peewee/SQLite (see
    metrics_framework/README.md #18's "database integration tests should
    separately verify the transformation from persisted records")."""

    def __init__(self, sessions=None, messages_by_session=None, signals_by_session=None):
        self._sessions = sessions or []
        self._messages_by_session = messages_by_session or {}
        self._signals_by_session = signals_by_session or {}

    def list_chat_sessions(self, username, project_name):
        return self._sessions

    def get_messages(self, session_id, last_n=None, since=None):
        return self._messages_by_session.get(session_id, [])

    def get_signals(self, session_id):
        return self._signals_by_session.get(session_id, [])


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestUserAnalyticsDataBuilder:
    def test_empty_db_builds_empty_frames_with_the_expected_columns(self):
        data = UserAnalyticsDataBuilder(FakeAnalyticsDb(), "user", "proj").build()

        assert data.username == "user"
        assert data.project_name == "proj"
        assert data.messages.empty
        assert data.sessions.empty
        assert data.signals.empty
        assert data.transitions.empty
        assert list(data.messages.columns) == ["id", "role", "content", "audio_text", "timestamp"]
        assert list(data.transitions.columns) == ["id", "timestamp", "values", "old_state", "action", "new_state"]

    def test_messages_are_pooled_across_every_session_and_sorted_chronologically(self):
        db = FakeAnalyticsDb(
            sessions=[
                session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
                session_row(2, datetime(2026, 1, 2), datetime(2026, 1, 2)),
            ],
            messages_by_session={
                1: [{"id": 1, "role": "user", "content": "second", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 2, 9))}],
                2: [{"id": 2, "role": "user", "content": "first", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1, 9))}],
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build()

        assert list(data.messages["content"]) == ["first", "second"]

    def test_signals_split_into_snapshots_and_transitions_by_new_state(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            signals_by_session={
                1: [
                    {"id": 1, "timestamp": _iso(datetime(2026, 1, 1, 9)), "values": json.dumps({"x": 1}), "old_state": None, "action": None, "new_state": None},
                    {"id": 2, "timestamp": _iso(datetime(2026, 1, 1, 10)), "values": None, "old_state": "a", "action": "advance", "new_state": "b"},
                ]
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build()

        assert len(data.signals) == 1
        assert data.signals.iloc[0]["id"] == 1
        assert len(data.transitions) == 1
        assert data.transitions.iloc[0]["id"] == 2

    def test_until_excludes_messages_after_the_cutoff(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            messages_by_session={
                1: [
                    {"id": 1, "role": "user", "content": "before", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1, 9))},
                    {"id": 2, "role": "user", "content": "after", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1, 11))},
                ]
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build(until=datetime(2026, 1, 1, 10))

        assert list(data.messages["content"]) == ["before"]

    def test_until_excludes_signals_after_the_cutoff(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            signals_by_session={
                1: [
                    signal_row(1, _iso(datetime(2026, 1, 1, 9)), values={"x": 1}),
                    signal_row(2, _iso(datetime(2026, 1, 1, 11)), values={"x": 2}),
                ]
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build(until=datetime(2026, 1, 1, 10))

        assert len(data.signals) == 1
        assert data.signals.iloc[0]["id"] == 1

    def test_until_excludes_sessions_that_had_not_started_yet(self):
        db = FakeAnalyticsDb(
            sessions=[
                session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
                session_row(2, datetime(2026, 1, 5), datetime(2026, 1, 5)),
            ],
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build(until=datetime(2026, 1, 2))

        assert list(data.sessions["id"]) == [1]

    def test_until_none_behaves_exactly_like_the_full_history(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            messages_by_session={
                1: [{"id": 1, "role": "user", "content": "hi", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1, 9))}]
            },
        )
        with_none = UserAnalyticsDataBuilder(db, "user", "proj").build(until=None)
        without_arg = UserAnalyticsDataBuilder(db, "user", "proj").build()

        assert list(with_none.messages["content"]) == list(without_arg.messages["content"])

    def test_user_messages_property_filters_by_role(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            messages_by_session={
                1: [
                    {"id": 1, "role": "user", "content": "hi", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1))},
                    {"id": 2, "role": "assistant", "content": "hello", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1))},
                ]
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build()

        assert list(data.user_messages["content"]) == ["hi"]


class TestTimelineSignalSeries:
    def test_no_signals_returns_an_empty_series(self):
        data = UserAnalyticsDataBuilder(FakeAnalyticsDb(), "user", "proj").build()
        series = Timeline(data).signal_series("x")
        assert series.empty

    def test_parses_json_string_and_dict_values_the_same_way(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            signals_by_session={
                1: [
                    {"id": 1, "timestamp": _iso(datetime(2026, 1, 1)), "values": json.dumps({"x": 10}), "old_state": None, "action": None, "new_state": None},
                    {"id": 2, "timestamp": _iso(datetime(2026, 1, 2)), "values": {"x": 20}, "old_state": None, "action": None, "new_state": None},
                ]
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build()
        series = Timeline(data).signal_series("x")
        assert list(series.values) == [10.0, 20.0]

    def test_ignores_missing_and_non_numeric_entries(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            signals_by_session={
                1: [
                    {"id": 1, "timestamp": _iso(datetime(2026, 1, 1)), "values": json.dumps({"y": 1}), "old_state": None, "action": None, "new_state": None},
                    {"id": 2, "timestamp": _iso(datetime(2026, 1, 2)), "values": json.dumps({"x": "not-a-number"}), "old_state": None, "action": None, "new_state": None},
                ]
            },
        )
        data = UserAnalyticsDataBuilder(db, "user", "proj").build()
        series = Timeline(data).signal_series("x")
        assert series.empty


class TestAnalyticsCalculator:
    def test_default_metrics_covers_all_five_core_metrics(self):
        names = {m.name for m in AnalyticsCalculator.default_metrics()}
        assert names == {"engagement", "retention", "activity_consistency", "state_stability", "signal_stability"}

    def test_every_default_metric_exposes_a_ui_label_and_description(self):
        for metric in AnalyticsCalculator.default_metrics():
            assert isinstance(metric.ui_label, str) and metric.ui_label
            assert isinstance(metric.ui_description, str) and metric.ui_description

    def test_calculate_all_returns_one_result_per_metric_in_order(self):
        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj")
        results = calculator.calculate_all()
        assert [r.name for r in results] == [m.name for m in calculator.metrics]

    def test_calculate_evaluates_a_single_metric_against_the_shared_dataset(self):
        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj")
        metric = calculator.metrics[0]
        result = calculator.calculate(metric)
        assert result.name == metric.name

    def test_custom_metrics_override_the_defaults(self):
        custom = AnalyticsCalculator.default_metrics()[:1]
        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj", metrics=custom)
        assert calculator.metrics == custom
        assert len(calculator.calculate_all()) == 1

    def test_until_restricts_metrics_to_the_history_at_or_before_it(self):
        db = FakeAnalyticsDb(
            sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))],
            messages_by_session={
                1: [
                    {"id": 1, "role": "user", "content": "hi", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1, 9))},
                    {"id": 2, "role": "user", "content": "hi2", "audio_text": None, "timestamp": _iso(datetime(2026, 1, 1, 11))},
                ]
            },
        )
        cutoff = AnalyticsCalculator(db, "user", "proj", until=datetime(2026, 1, 1, 10))
        full = AnalyticsCalculator(db, "user", "proj")

        cutoff_engagement = {r.name: r.value for r in cutoff.calculate_all()}["engagement"]
        full_engagement = {r.name: r.value for r in full.calculate_all()}["engagement"]

        assert cutoff_engagement < full_engagement

    def test_empty_history_scores_every_metric_at_or_near_the_floor(self):
        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj")
        results = {r.name: r.value for r in calculator.calculate_all()}
        assert results["engagement"] == 0.0
        assert results["state_stability"] == 100.0  # "no evidence of instability" reads as stable
        assert results["signal_stability"] == 0.0

    def test_default_metrics_are_filtered_to_a_one_session_context(self):
        """Every current caller (chat/metrics_service.py's ChatMetrics, for
        both the "Benchmark"/"Edit project" views' own metrics displays and
        trigger evaluation) only ever runs within one session — retention/
        activity_consistency's own scope excludes that (see
        RetentionMetric/ActivityConsistencyMetric.scope), so neither is
        ever part of the default set an instance actually evaluates,
        unlike the *full*, unfiltered registry default_metrics() itself
        returns (see test_default_metrics_covers_all_five_core_metrics)."""
        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj")
        assert {m.name for m in calculator.metrics} == {"engagement", "state_stability", "signal_stability"}
