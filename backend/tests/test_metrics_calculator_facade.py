from __future__ import annotations

import json
from datetime import datetime

import pytest

from metrics.metrics_framework import AnalyticsCalculator
from metrics.metrics_framework.timeline import Timeline, UserAnalyticsDataBuilder
from metrics_helpers import session_row, signal_row


class FakeAnalyticsDb:
    """Protocol-shaped fake that exercises UserAnalyticsDataBuilder/
    AnalyticsCalculator the way the real Db facade is consumed, without
    touching Peewee/SQLite."""

    def __init__(self, sessions=None, messages_by_session=None, signals_by_session=None):
        self._sessions = sessions or []
        self._messages_by_session = messages_by_session or {}
        self._signals_by_session = signals_by_session or {}

    def list_chat_sessions(self, username, project_id):
        return self._sessions

    def get_messages(self, session_id, last_n=None, since=None):
        return self._messages_by_session.get(session_id, [])

    def get_signals(self, session_id):
        return self._signals_by_session.get(session_id, [])


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _message(id_: int, content: str, at: datetime, role: str = "user") -> dict:
    return {"id": id_, "role": role, "content": content, "audio_text": None, "timestamp": _iso(at)}


def _signal(id_: int, at: datetime, values=None, old_state=None, action=None, new_state=None) -> dict:
    return {
        "id": id_, "timestamp": _iso(at), "values": values,
        "old_state": old_state, "action": action, "new_state": new_state,
    }


def _one_session(**kwargs) -> FakeAnalyticsDb:
    return FakeAnalyticsDb(sessions=[session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1))], **kwargs)


def _build(db: FakeAnalyticsDb, **kwargs):
    return UserAnalyticsDataBuilder(db, "user", "proj").build(**kwargs)


class TestUserAnalyticsDataBuilder:
    @pytest.mark.contract
    def test_empty_db_builds_empty_frames_with_the_expected_columns(self):
        data = _build(FakeAnalyticsDb())

        assert data.username == "user"
        assert data.project_id == "proj"
        assert data.messages.empty
        assert data.sessions.empty
        assert data.signals.empty
        assert data.transitions.empty
        assert list(data.messages.columns) == ["id", "role", "content", "audio_text", "timestamp"]
        assert list(data.transitions.columns) == [
            "id", "timestamp", "values", "old_state", "action", "new_state", "message_id",
        ]

    @pytest.mark.contract
    def test_messages_pool_across_sessions_sorted_chronologically_signals_split_by_new_state_and_user_messages_filter_by_role(self):
        db = FakeAnalyticsDb(
            sessions=[
                session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
                session_row(2, datetime(2026, 1, 2), datetime(2026, 1, 2)),
            ],
            messages_by_session={
                1: [_message(1, "second", datetime(2026, 1, 2, 9)), _message(3, "reply", datetime(2026, 1, 2, 10), role="assistant")],
                2: [_message(2, "first", datetime(2026, 1, 1, 9))],
            },
            signals_by_session={
                1: [
                    _signal(1, datetime(2026, 1, 1, 9), values=json.dumps({"x": 1})),
                    _signal(2, datetime(2026, 1, 1, 10), old_state="a", action="advance", new_state="b"),
                ]
            },
        )
        data = _build(db)

        assert list(data.messages["content"]) == ["first", "second", "reply"]
        assert list(data.user_messages["content"]) == ["first", "second"]
        assert len(data.signals) == 1
        assert data.signals.iloc[0]["id"] == 1
        assert len(data.transitions) == 1
        assert data.transitions.iloc[0]["id"] == 2

    @pytest.mark.contract
    def test_until_excludes_later_messages_signals_and_unstarted_sessions_while_none_means_full_history(self):
        db = FakeAnalyticsDb(
            sessions=[
                session_row(1, datetime(2026, 1, 1), datetime(2026, 1, 1)),
                session_row(2, datetime(2026, 1, 5), datetime(2026, 1, 5)),
            ],
            messages_by_session={1: [_message(1, "before", datetime(2026, 1, 1, 9)), _message(2, "after", datetime(2026, 1, 1, 11))]},
            signals_by_session={1: [signal_row(1, _iso(datetime(2026, 1, 1, 9)), values={"x": 1}), signal_row(2, _iso(datetime(2026, 1, 1, 11)), values={"x": 2})]},
        )

        cut = _build(db, until=datetime(2026, 1, 1, 10))
        assert list(cut.messages["content"]) == ["before"]
        assert list(cut.signals["id"]) == [1]
        assert list(cut.sessions["id"]) == [1]

        assert list(_build(db, until=None).messages["content"]) == list(_build(db).messages["content"]) == ["before", "after"]

    @pytest.mark.contract
    def test_since_excludes_earlier_messages_and_ended_sessions_and_combines_with_until_into_a_window(self):
        db = FakeAnalyticsDb(
            sessions=[
                session_row(1, datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 10)),
                session_row(2, datetime(2026, 1, 5), datetime(2026, 1, 5)),
            ],
            messages_by_session={
                1: [
                    _message(1, "too-early", datetime(2026, 1, 1, 8)),
                    _message(2, "in-window", datetime(2026, 1, 1, 11)),
                    _message(3, "too-late", datetime(2026, 1, 1, 13)),
                ]
            },
        )

        since = _build(db, since=datetime(2026, 1, 1, 10))
        assert list(since.messages["content"]) == ["in-window", "too-late"]
        assert list(since.sessions["id"]) == [1, 2]
        assert list(_build(db, since=datetime(2026, 1, 2)).sessions["id"]) == [2]

        windowed = _build(db, since=datetime(2026, 1, 1, 10), until=datetime(2026, 1, 1, 12))
        assert list(windowed.messages["content"]) == ["in-window"]


class TestTimelineSignalSeries:
    @pytest.mark.contract
    def test_is_empty_without_usable_values_and_parses_json_strings_and_dicts_alike(self):
        assert Timeline(_build(FakeAnalyticsDb())).signal_series("x").empty

        unusable = _one_session(signals_by_session={1: [
            _signal(1, datetime(2026, 1, 1), values=json.dumps({"y": 1})),
            _signal(2, datetime(2026, 1, 2), values=json.dumps({"x": "not-a-number"})),
        ]})
        assert Timeline(_build(unusable)).signal_series("x").empty

        mixed = _one_session(signals_by_session={1: [
            _signal(1, datetime(2026, 1, 1), values=json.dumps({"x": 10})),
            _signal(2, datetime(2026, 1, 2), values={"x": 20}),
        ]})
        assert list(Timeline(_build(mixed)).signal_series("x").values) == [10.0, 20.0]


class TestAnalyticsCalculator:
    @pytest.mark.contract
    def test_default_metrics_are_labelled_and_filtered_to_a_one_session_context_unless_overridden(self):
        """Every caller runs within one session — retention/
        activity_consistency's scope excludes that, so neither is part of
        the default set an instance actually evaluates."""
        defaults = AnalyticsCalculator.default_metrics()
        assert {m.name for m in defaults} == {"engagement", "retention", "activity_consistency", "state_stability", "signal_stability"}
        for metric in defaults:
            assert isinstance(metric.ui_label, str) and metric.ui_label
            assert isinstance(metric.ui_description, str) and metric.ui_description

        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj")
        assert {m.name for m in calculator.metrics} == {"engagement", "state_stability", "signal_stability"}

        custom = defaults[:1]
        overridden = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj", metrics=custom)
        assert overridden.metrics == custom
        assert len(overridden.calculate_all()) == 1

    @pytest.mark.regression
    def test_calculate_all_returns_one_result_per_metric_in_order_scoring_an_empty_history_at_the_floor(self):
        calculator = AnalyticsCalculator(FakeAnalyticsDb(), "user", "proj")
        results = calculator.calculate_all()
        assert [r.name for r in results] == [m.name for m in calculator.metrics]
        assert calculator.calculate(calculator.metrics[0]).name == calculator.metrics[0].name

        by_name = {r.name: r.value for r in results}
        assert by_name["engagement"] == 0.0
        assert by_name["state_stability"] == 100.0
        assert by_name["signal_stability"] == 0.0

    @pytest.mark.regression
    def test_until_and_since_each_restrict_metrics_to_their_side_of_the_cutoff(self):
        db = _one_session(messages_by_session={1: [_message(1, "hi", datetime(2026, 1, 1, 9)), _message(2, "hi2", datetime(2026, 1, 1, 11))]})

        def _engagement(calculator: AnalyticsCalculator) -> float:
            return {r.name: r.value for r in calculator.calculate_all()}["engagement"]

        full = _engagement(AnalyticsCalculator(db, "user", "proj"))
        assert _engagement(AnalyticsCalculator(db, "user", "proj", until=datetime(2026, 1, 1, 10))) < full
        assert _engagement(AnalyticsCalculator(db, "user", "proj", since=datetime(2026, 1, 1, 10))) < full
