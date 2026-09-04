from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from db.models import AiTokenUsage

pytestmark = pytest.mark.regression


def test_history_buckets_by_minute_and_ignores_other_providers(db):
    earlier_minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=90)
    later_minute = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    AiTokenUsage.create(provider_label="p1", timestamp=earlier_minute, input_tokens=10, output_tokens=5)
    AiTokenUsage.create(provider_label="p1", timestamp=earlier_minute + timedelta(seconds=10), input_tokens=1, output_tokens=1)
    AiTokenUsage.create(provider_label="p1", timestamp=later_minute, input_tokens=100, output_tokens=50)
    AiTokenUsage.create(provider_label="p2", timestamp=later_minute, input_tokens=7, output_tokens=3)
    AiTokenUsage.create(provider_label="untracked", timestamp=later_minute, input_tokens=999, output_tokens=999)

    snapshot = db.get_ai_token_usage_snapshot(["p1", "p2"], hours=24)

    assert len(snapshot["history"]) == 2
    assert snapshot["history"][0]["values"] == {"p1": 17}
    assert snapshot["history"][1]["values"] == {"p1": 150, "p2": 10}
    assert snapshot["today"] == {"p1": 167, "p2": 10}


def test_history_excludes_rows_older_than_the_window(db):
    now = datetime.utcnow()
    AiTokenUsage.create(provider_label="p1", timestamp=now - timedelta(hours=48), input_tokens=10, output_tokens=0)
    AiTokenUsage.create(provider_label="p1", timestamp=now - timedelta(minutes=1), input_tokens=5, output_tokens=0)

    snapshot = db.get_ai_token_usage_snapshot(["p1"], hours=24)

    assert len(snapshot["history"]) == 1
    assert snapshot["history"][0]["values"] == {"p1": 5}


def test_today_ignores_the_hours_window_and_counts_the_whole_calendar_day(db):
    midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    AiTokenUsage.create(provider_label="p1", timestamp=midnight + timedelta(minutes=1), input_tokens=3, output_tokens=0)

    snapshot = db.get_ai_token_usage_snapshot(["p1"], hours=1)

    assert snapshot["today"] == {"p1": 3}


def test_empty_provider_labels_short_circuits(db):
    AiTokenUsage.create(provider_label="p1", timestamp=datetime.utcnow(), input_tokens=3, output_tokens=0)

    assert db.get_ai_token_usage_snapshot([]) == {"today": {}, "history": []}
