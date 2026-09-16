"""Db-level tests for local_memory-only Tracking rows
(Db.get_local_memory/set_local_memory/clear_local_memory) — the
`ai-memory-scope: local` cell. Unlike get_env/set_env (scoped per
(user, project), shared across every session of that pair), this one is
scoped per session_id alone.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from db.models import Tracking

pytestmark = pytest.mark.regression


def _session(db, username="user", project_name="proj", start=None):
    start = start or datetime(2026, 1, 1)
    db.ensure_project(project_name)
    db.publish_project(project_name)
    return db.create_chat_session(
        username=username, project_id=project_name,
        revision=db.get_project_published_revision(project_name),
        datetime_start=start, datetime_end=start,
        start_state="a", end_state="a",
    )


def test_local_memory_round_trips_scoped_per_session_always_reporting_the_latest_write(db):
    assert db.get_local_memory(999999) == {}

    first = _session(db, username="alice")
    second = _session(db, username="alice")

    db.set_local_memory(first, {"a": "1"})
    db.set_local_memory(first, {"note": "collected"})
    db.set_local_memory(second, {"elsewhere": "1"})

    assert db.get_local_memory(first) == {"note": "collected"}
    assert db.get_local_memory(second) == {"elsewhere": "1"}


def test_two_sessions_of_the_same_user_and_project_never_see_each_others_local_memory(db):
    """Unlike get_env/set_env, which are keyed by (project, user) and so
    are shared across every session of that pair."""
    session_a = _session(db, username="alice")
    session_b = _session(db, username="alice")

    db.set_local_memory(session_a, {"note": "from session a"})

    assert db.get_local_memory(session_b) == {}


def test_clear_local_memory_empties_the_cell(db):
    session_id = _session(db)
    db.set_local_memory(session_id, {"note": "collected"})

    db.clear_local_memory(session_id)

    assert db.get_local_memory(session_id) == {}


def test_local_memory_until_resolves_to_the_value_as_of_that_point_in_time(db):
    session_id = _session(db)
    Tracking.create(session=session_id, local_memory=json.dumps({"note": "first"}), timestamp=datetime(2026, 1, 1, 9, 0))
    Tracking.create(session=session_id, local_memory=json.dumps({"note": "second"}), timestamp=datetime(2026, 1, 1, 9, 0, 30))

    assert db.get_local_memory(session_id, until=datetime(2026, 1, 1, 9, 0, 15)) == {"note": "first"}
    assert db.get_local_memory(session_id) == {"note": "second"}
