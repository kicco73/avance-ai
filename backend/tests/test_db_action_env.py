"""Db-level tests for action-env-only Tracking rows (Db.get_action_env/
set_action_env) — values an action's YAML `env:` field sets, kept apart
from get_env/set_env's model-reported values.
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


def test_get_action_env_is_empty_for_an_unknown_user_and_project(db):
    assert db.get_action_env("proj", "nobody") == {}


def test_set_action_env_then_get_action_env_round_trips(db):
    session_id = _session(db)
    db.set_action_env(session_id, {"number_of_steps": 3})

    assert db.get_action_env("proj", "user") == {"number_of_steps": 3}


def test_set_action_env_never_updates_in_place_but_get_still_sees_the_latest(db):
    session_id = _session(db)
    db.set_action_env(session_id, {"a": 1})

    db.set_action_env(session_id, {"b": 2})

    assert db.get_action_env("proj", "user") == {"b": 2}


def test_action_env_is_scoped_per_user_and_project(db):
    alice_session = _session(db, username="alice")
    _session(db, username="bob")
    db.set_action_env(alice_session, {"a": 1})

    assert db.get_action_env("proj", "alice") == {"a": 1}
    assert db.get_action_env("proj", "bob") == {}
    assert db.get_action_env("other-proj", "alice") == {}


def test_action_env_is_independent_of_env(db):
    """The `env` and `action_env` columns never leak into each other, even
    for the same (user, project)."""
    session_id = _session(db)
    db.set_env(session_id, {"favorite_color": "blue"})
    db.set_action_env(session_id, {"number_of_steps": 1})

    assert db.get_env("proj", "user") == {"favorite_color": "blue"}
    assert db.get_action_env("proj", "user") == {"number_of_steps": 1}


def test_action_env_only_rows_are_excluded_from_get_signals(db):
    session_id = _session(db)
    db.set_action_env(session_id, {"a": 1})

    assert db.get_signals(session_id) == []


def test_reset_project_wipes_action_env_for_every_user_of_that_project(db):
    alice_session = _session(db, username="alice")
    bob_session = _session(db, username="bob")
    db.set_action_env(alice_session, {"a": 1})
    db.set_action_env(bob_session, {"a": 1})

    db.reset_project("proj")

    assert db.get_action_env("proj", "alice") == {}
    assert db.get_action_env("proj", "bob") == {}


def test_set_action_env_writes_onto_the_given_session_never_the_latest_live_one(db):
    older = _session(db, start=datetime(2026, 1, 1))
    newer = _session(db, start=datetime(2026, 1, 2))
    assert db.get_latest_chat_session("user", "proj")["id"] == newer

    db.set_action_env(older, {"a": 1})

    row = Tracking.get(Tracking.action_env.is_null(False))
    assert row.session_id == older


def test_reset_project_for_user_also_wipes_their_action_env(db):
    session_id = _session(db, username="alice")
    db.set_action_env(session_id, {"a": 1})

    db.reset_project_for_user("alice", "proj", type="live")

    assert db.get_action_env("proj", "alice") == {}
