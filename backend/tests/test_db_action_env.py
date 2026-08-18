"""Db-level tests for action-env-only Tracking rows (see
db.Db.get_action_env/set_action_env, and Tracking's own docstring) — the
persisted half of chat.env.Env's action_set/update_action_set, for values
an action's own YAML `env:` field sets (see automaton_builder.py's
_build_action/Automaton.eval_action_env), kept apart from get_env/set_env's
own model-reported values (see test_db_env.py, which this mirrors) so the
Inspector Env tab can badge the two apart ("SET" vs "AI").
"""
from __future__ import annotations

from datetime import datetime

import pytest

# Every test in this file verifies a specific persistence behavior/fact
# (round-tripping, scoping, no-op semantics) rather than a response shape —
# all regression.
pytestmark = pytest.mark.regression


def _session(db, username="user", project_name="proj", start=None):
    start = start or datetime(2026, 1, 1)
    db.ensure_project(project_name)
    db.publish_project(project_name)
    return db.create_chat_session(
        username=username, project_name=project_name,
        datetime_start=start, datetime_end=start,
        start_state="a", end_state="a",
    )


def test_get_action_env_is_empty_for_an_unknown_user_and_project(db):
    assert db.get_action_env("proj", "nobody") == {}


def test_set_action_env_is_a_noop_without_any_existing_session(db):
    db.set_action_env("proj", {"a": 1}, "user")
    assert db.get_action_env("proj", "user") == {}


def test_set_action_env_then_get_action_env_round_trips(db):
    _session(db)
    db.set_action_env("proj", {"number_of_steps": 3}, "user")

    assert db.get_action_env("proj", "user") == {"number_of_steps": 3}


def test_set_action_env_never_updates_in_place_but_get_still_sees_the_latest(db):
    _session(db)
    db.set_action_env("proj", {"a": 1}, "user")

    db.set_action_env("proj", {"b": 2}, "user")

    assert db.get_action_env("proj", "user") == {"b": 2}


def test_action_env_is_scoped_per_user_and_project(db):
    _session(db, username="alice")
    _session(db, username="bob")
    db.set_action_env("proj", {"a": 1}, "alice")

    assert db.get_action_env("proj", "alice") == {"a": 1}
    assert db.get_action_env("proj", "bob") == {}
    assert db.get_action_env("other-proj", "alice") == {}


def test_action_env_is_independent_of_env(db):
    """The two stores (see Tracking's own docstring — `env` vs
    `action_env` columns) never leak into each other, even for the same
    (user, project)."""
    _session(db)
    db.set_env("proj", {"favorite_color": "blue"}, "user")
    db.set_action_env("proj", {"number_of_steps": 1}, "user")

    assert db.get_env("proj", "user") == {"favorite_color": "blue"}
    assert db.get_action_env("proj", "user") == {"number_of_steps": 1}


def test_action_env_only_rows_are_excluded_from_get_signals(db):
    session_id = _session(db)
    db.set_action_env("proj", {"a": 1}, "user")

    assert db.get_signals(session_id) == []


def test_reset_project_wipes_action_env_for_every_user_of_that_project(db):
    _session(db, username="alice")
    _session(db, username="bob")
    db.set_action_env("proj", {"a": 1}, "alice")
    db.set_action_env("proj", {"a": 1}, "bob")

    db.reset_project("proj")

    assert db.get_action_env("proj", "alice") == {}
    assert db.get_action_env("proj", "bob") == {}


def test_reset_project_for_user_also_wipes_their_action_env(db):
    _session(db, username="alice")
    db.set_action_env("proj", {"a": 1}, "alice")

    db.reset_project_for_user("alice", "proj")

    assert db.get_action_env("proj", "alice") == {}
