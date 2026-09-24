from __future__ import annotations

import sqlite3

import pytest

from db.db import Db
from db.models import DbUsage

pytestmark = pytest.mark.contract


def _minute_rows(query_name: str) -> list[DbUsage]:
    return list(DbUsage.select().where(DbUsage.query_name == query_name))


def test_every_call_of_a_minute_is_one_row_with_its_count_and_durations(db):
    for _ in range(5):
        db.project_exists("nothing")

    snapshot = db.get_db_usage_snapshot()

    assert sum(point["values"].get("project_exists", 0) for point in snapshot["request_history"]) == 5
    rows = _minute_rows("project_exists")
    assert sum(row.count for row in rows) == 5
    for row in rows:
        assert row.kind == "read" and row.outcome == "success"
        assert 0 < row.median_duration <= row.max_duration
        assert 0 < row.average_duration <= row.max_duration


def test_a_failed_call_is_counted_apart_from_the_successful_ones(db):
    db.project_exists("nothing")
    with pytest.raises(Exception):
        db.settle_task("no-such-task", "not-a-status")

    snapshot = db.get_db_usage_snapshot()

    assert sum(point["values"].get("settle_task", 0) for point in snapshot["error_history"]) == 1
    assert [(row.kind, row.outcome, row.count) for row in _minute_rows("settle_task")] == [("write", "failure", 1)]


def test_starting_on_a_database_of_raw_calls_folds_them_into_minutes(tmp_path):
    path = tmp_path / "working.db"
    Db(f"sqlite:///{path}")
    connection = sqlite3.connect(path)
    connection.execute('DROP TABLE "DbUsage"')
    connection.execute(
        'CREATE TABLE "DbUsage" ("id" INTEGER NOT NULL PRIMARY KEY, "query_name" VARCHAR(255) NOT NULL, '
        '"timestamp" DATETIME NOT NULL, "duration" REAL NOT NULL, "outcome" VARCHAR(255) NOT NULL, '
        '"kind" VARCHAR(255) NOT NULL)'
    )
    connection.executemany(
        'INSERT INTO "DbUsage" ("query_name", "timestamp", "duration", "outcome", "kind") VALUES (?, ?, ?, ?, ?)',
        [
            ("get_user_by_id", "2026-09-23 16:37:01.100000", 1.0, "success", "read"),
            ("get_user_by_id", "2026-09-23 16:37:30.200000", 6.0, "success", "read"),
            ("get_user_by_id", "2026-09-23 16:37:59.900000", 2.0, "success", "read"),
            ("get_user_by_id", "2026-09-23 16:37:40.000000", 5.0, "failure", "read"),
            ("get_user_by_id", "2026-09-23 16:38:00.000000", 4.0, "success", "read"),
        ],
    )
    connection.commit()
    connection.close()

    Db(f"sqlite:///{path}", migration_strategy="upgrade")

    rows = sorted(
        (str(row.timestamp), row.outcome, row.count, row.average_duration, row.median_duration, row.max_duration)
        for row in _minute_rows("get_user_by_id")
    )
    assert rows == [
        ("2026-09-23 16:37:00", "failure", 1, 5.0, 5.0, 5.0),
        ("2026-09-23 16:37:00", "success", 3, 3.0, 2.0, 6.0),
        ("2026-09-23 16:38:00", "success", 1, 4.0, 4.0, 4.0),
    ]
