"""Db-level tests for env-only Tracking rows (Db.get_env/set_env) — the
per-(user, project) "environment" memory. Env lives and dies with
whatever session it was recorded under, like any other Tracking row.
"""
from __future__ import annotations

from datetime import datetime

import pytest

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


def test_get_env_is_empty_for_an_unknown_user_and_project(db):
    assert db.get_env("proj", "nobody") == {}


def test_set_env_is_a_noop_without_any_existing_session(db):
    db.set_env("proj", {"a": "1"}, "user")
    assert db.get_env("proj", "user") == {}


def test_set_env_then_get_env_round_trips(db):
    _session(db)
    db.set_env("proj", {"favorite_color": "blue"}, "user")

    assert db.get_env("proj", "user") == {"favorite_color": "blue"}


def test_set_env_never_updates_in_place_but_get_env_still_sees_the_latest(db):
    """Each set_env is a new row — get_env always resolves to whichever
    is most recent."""
    _session(db)
    db.set_env("proj", {"a": "1"}, "user")

    db.set_env("proj", {"b": "2"}, "user")

    assert db.get_env("proj", "user") == {"b": "2"}


def test_env_is_scoped_per_user(db):
    _session(db, username="alice")
    _session(db, username="bob")
    db.set_env("proj", {"a": "1"}, "alice")

    assert db.get_env("proj", "alice") == {"a": "1"}
    assert db.get_env("proj", "bob") == {}


def test_env_is_scoped_per_project_too(db):
    """The same user's env for one project must not leak into another —
    every (user, project) pair is independent."""
    _session(db, project_name="proj-a")
    db.set_env("proj-a", {"a": "1"}, "user")

    assert db.get_env("proj-a", "user") == {"a": "1"}
    assert db.get_env("proj-b", "user") == {}


def test_env_only_rows_are_excluded_from_get_signals(db):
    session_id = _session(db)
    db.set_env("proj", {"a": "1"}, "user")

    assert db.get_signals(session_id) == []


def test_reset_project_wipes_env_for_every_user_of_that_project(db):
    _session(db, username="alice")
    _session(db, username="bob")
    _session(db, username="alice", project_name="other-proj")
    db.set_env("proj", {"a": "1"}, "alice")
    db.set_env("proj", {"a": "1"}, "bob")
    db.set_env("other-proj", {"a": "1"}, "alice")

    db.reset_project("proj")

    assert db.get_env("proj", "alice") == {}
    assert db.get_env("proj", "bob") == {}
    # A different project's env is untouched.
    assert db.get_env("other-proj", "alice") == {"a": "1"}


def test_reset_project_for_user_also_wipes_their_env(db):
    """Env is FK'd to its session and cascade-deleted with it, so a
    "Reset conversation" removes it too, like any other Tracking row."""
    _session(db, username="alice")
    db.set_env("proj", {"a": "1"}, "alice")

    db.reset_project_for_user("alice", "proj", source="native")

    assert db.get_env("proj", "alice") == {}
