from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from db.models import AiUsage

pytestmark = pytest.mark.regression


def test_history_buckets_by_minute_and_ignores_other_providers(db):
    earlier_minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=90)
    later_minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    AiUsage.create(provider_label="p1", timestamp=earlier_minute, input_tokens=10, output_tokens=5)
    AiUsage.create(provider_label="p1", timestamp=earlier_minute + timedelta(seconds=10), input_tokens=1, output_tokens=1)
    AiUsage.create(provider_label="p1", timestamp=later_minute, input_tokens=100, output_tokens=50)
    AiUsage.create(provider_label="p2", timestamp=later_minute, input_tokens=7, output_tokens=3)
    AiUsage.create(provider_label="untracked", timestamp=later_minute, input_tokens=999, output_tokens=999)

    snapshot = db.get_ai_usage_snapshot(["p1", "p2"], hours=24)

    assert len(snapshot["history"]) == 2
    assert snapshot["history"][0]["values"] == {"p1": 17}
    assert snapshot["history"][1]["values"] == {"p1": 150, "p2": 10}
    assert snapshot["today"] == {"p1": 167, "p2": 10}


def test_history_excludes_rows_older_than_the_window(db):
    now = datetime.utcnow()
    AiUsage.create(provider_label="p1", timestamp=now - timedelta(hours=48), input_tokens=10, output_tokens=0)
    AiUsage.create(provider_label="p1", timestamp=now - timedelta(minutes=1), input_tokens=5, output_tokens=0)

    snapshot = db.get_ai_usage_snapshot(["p1"], hours=24)

    assert len(snapshot["history"]) == 1
    assert snapshot["history"][0]["values"] == {"p1": 5}


def test_today_ignores_the_hours_window_and_counts_the_whole_calendar_day(db):
    midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    AiUsage.create(provider_label="p1", timestamp=midnight + timedelta(minutes=1), input_tokens=3, output_tokens=0)

    snapshot = db.get_ai_usage_snapshot(["p1"], hours=1)

    assert snapshot["today"] == {"p1": 3}


def test_empty_provider_labels_short_circuits(db):
    AiUsage.create(provider_label="p1", timestamp=datetime.utcnow(), input_tokens=3, output_tokens=0)

    assert db.get_ai_usage_snapshot([]) == {
        "today": {}, "today_cache_read": {}, "history": [], "provider_changes": [], "error_history": [], "cache_read_ratio": {},
    }


def test_cache_read_tokens_are_broken_out_per_minute_today_and_as_a_ratio_of_input(db):
    minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    AiUsage.create(
        provider_label="p1", timestamp=minute, input_tokens=100, output_tokens=20,
        cache_read_tokens=80, cache_creation_tokens=5,
    )
    AiUsage.create(
        provider_label="p1", timestamp=minute + timedelta(seconds=10), input_tokens=50, output_tokens=10,
        cache_read_tokens=0, cache_creation_tokens=0,
    )

    snapshot = db.get_ai_usage_snapshot(["p1"], hours=24)

    assert snapshot["history"][0]["cache_read"] == {"p1": 80}
    assert snapshot["today_cache_read"] == {"p1": 80}
    assert snapshot["cache_read_ratio"]["p1"] == 80 / 150


def test_cache_read_ratio_is_empty_for_a_provider_with_no_rows_at_all(db):
    assert db.get_ai_usage_snapshot(["p1"])["cache_read_ratio"] == {}


def test_history_carries_each_provider_s_mean_call_duration_per_minute(db):
    minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    AiUsage.create(provider_label="p1", timestamp=minute, input_tokens=1, output_tokens=1, duration=1.0)
    AiUsage.create(provider_label="p1", timestamp=minute + timedelta(seconds=20), input_tokens=1, output_tokens=1, duration=3.0)
    AiUsage.create(provider_label="p2", timestamp=minute, input_tokens=1, output_tokens=1, duration=0.5)
    AiUsage.create(provider_label="p1", timestamp=minute + timedelta(minutes=1), input_tokens=1, output_tokens=1, duration=4.0)

    history = db.get_ai_usage_snapshot(["p1", "p2"], hours=24)["history"]

    assert [entry["duration"] for entry in history] == [{"p1": 2.0, "p2": 0.5}, {"p1": 4.0}]


def test_provider_changes_mark_each_call_whose_provider_differs_from_the_one_before(db):
    start = datetime.utcnow() - timedelta(minutes=10)
    AiUsage.create(provider_label="p1", timestamp=start, input_tokens=1, output_tokens=1)
    AiUsage.create(provider_label="p1", timestamp=start + timedelta(minutes=1), input_tokens=1, output_tokens=1)
    AiUsage.create(provider_label="p2", timestamp=start + timedelta(minutes=2), input_tokens=1, output_tokens=1)
    AiUsage.create(provider_label="p2", timestamp=start + timedelta(minutes=3), input_tokens=1, output_tokens=1)
    AiUsage.create(provider_label="p1", timestamp=start + timedelta(minutes=4), input_tokens=1, output_tokens=1)

    changes = db.get_ai_usage_snapshot(["p1", "p2"], hours=24)["provider_changes"]

    assert changes == [
        {"timestamp": f"{(start + timedelta(minutes=2)).isoformat()}+00:00", "provider_label": "p2"},
        {"timestamp": f"{(start + timedelta(minutes=4)).isoformat()}+00:00", "provider_label": "p1"},
    ]


def test_error_history_buckets_each_non_success_outcome_in_one_minute_slots(db):
    slot_start = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=2)
    AiUsage.create(provider_label="p1", timestamp=slot_start, outcome="rate_limited")
    AiUsage.create(provider_label="p1", timestamp=slot_start + timedelta(seconds=30), outcome="rate_limited")
    AiUsage.create(provider_label="p1", timestamp=slot_start + timedelta(seconds=45), outcome="unavailable")
    AiUsage.create(provider_label="p1", timestamp=slot_start + timedelta(minutes=1), outcome="permanent")
    AiUsage.create(provider_label="p1", timestamp=slot_start, input_tokens=1, output_tokens=1)

    error_history = db.get_ai_usage_snapshot(["p1"], hours=24)["error_history"]

    assert error_history == [
        {"timestamp": f"{slot_start.isoformat()}+00:00", "values": {"rate_limited": 2, "unavailable": 1}},
        {"timestamp": f"{(slot_start + timedelta(minutes=1)).isoformat()}+00:00", "values": {"permanent": 1}},
    ]


def test_error_history_ignores_outcomes_from_other_providers(db):
    now = datetime.utcnow()
    AiUsage.create(provider_label="untracked", timestamp=now, outcome="permanent")

    assert db.get_ai_usage_snapshot(["p1"])["error_history"] == []


def test_token_and_duration_history_exclude_failed_calls(db):
    minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    AiUsage.create(provider_label="p1", timestamp=minute, input_tokens=10, output_tokens=5, duration=1.0)
    AiUsage.create(provider_label="p1", timestamp=minute, outcome="unavailable", duration=99.0)

    history = db.get_ai_usage_snapshot(["p1"], hours=24)["history"]

    assert history == [{
        "timestamp": f"{minute.isoformat()}+00:00", "values": {"p1": 15}, "cache_read": {"p1": 0}, "duration": {"p1": 1.0},
        "time_to_first_chunk": {},
    }]


def test_history_carries_the_mean_time_to_first_chunk_omitting_calls_that_never_produced_one(db):
    minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    AiUsage.create(provider_label="p1", timestamp=minute, input_tokens=1, output_tokens=1, time_to_first_chunk=0.5)
    AiUsage.create(provider_label="p1", timestamp=minute, input_tokens=1, output_tokens=1, time_to_first_chunk=1.5)
    AiUsage.create(provider_label="p1", timestamp=minute, input_tokens=1, output_tokens=1, time_to_first_chunk=None)
    AiUsage.create(provider_label="p2", timestamp=minute, input_tokens=1, output_tokens=1, time_to_first_chunk=None)

    history = db.get_ai_usage_snapshot(["p1", "p2"], hours=24)["history"]

    assert history[0]["time_to_first_chunk"] == {"p1": 1.0}
