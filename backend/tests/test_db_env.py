"""Db-level tests for env-only Tracking rows (Db.get_env/set_env) — the
per-(user, project) "environment" memory. Env lives and dies with
whatever session it was recorded under, like any other Tracking row.
"""
from __future__ import annotations

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


def test_env_round_trips_scoped_per_user_and_project_always_reporting_the_latest_write(db):
    """Each set_env is a new row — get_env always resolves to whichever is
    most recent, and every (user, project) pair is independent."""
    assert db.get_env("proj", "nobody") == {}

    alice_session = _session(db, username="alice")
    _session(db, username="bob")
    other_project = _session(db, username="alice", project_name="proj-b")

    db.set_env(alice_session, {"a": "1"})
    db.set_env(alice_session, {"favorite_color": "blue"})
    db.set_env(other_project, {"elsewhere": "1"})

    assert db.get_env("proj", "alice") == {"favorite_color": "blue"}
    assert db.get_env("proj", "bob") == {}
    assert db.get_env("proj-b", "alice") == {"elsewhere": "1"}


def test_env_only_rows_never_show_up_in_get_signals(db):
    session_id = _session(db)
    db.set_env(session_id, {"a": "1"})

    assert db.get_signals(session_id) == []


def test_set_env_writes_onto_the_given_session_never_the_latest_live_one(db):
    older = _session(db, start=datetime(2026, 1, 1))
    newer = _session(db, start=datetime(2026, 1, 2))
    assert db.get_latest_chat_session("user", "proj")["id"] == newer

    db.set_env(older, {"a": "1"})

    assert Tracking.get(Tracking.env.is_null(False)).session_id == older


def test_resetting_a_project_wholesale_or_for_one_user_wipes_the_matching_env(db):
    """Env is FK'd to its session and cascade-deleted with it, so a
    "Reset conversation" removes it too, like any other Tracking row."""
    alice_session = _session(db, username="alice")
    bob_session = _session(db, username="bob")
    other_project = _session(db, username="alice", project_name="other-proj")
    db.set_env(alice_session, {"a": "1"})
    db.set_env(bob_session, {"a": "1"})
    db.set_env(other_project, {"a": "1"})

    db.reset_project_for_user("alice", "proj", type="live")
    assert db.get_env("proj", "alice") == {}
    assert db.get_env("proj", "bob") == {"a": "1"}

    db.reset_project("proj")
    assert db.get_env("proj", "bob") == {}
    # A different project's env is untouched throughout.
    assert db.get_env("other-proj", "alice") == {"a": "1"}
