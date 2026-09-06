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


def test_action_env_round_trips_per_user_and_project_always_reporting_the_latest_write(db):
    assert db.get_action_env("proj", "nobody") == {}

    alice_session = _session(db, username="alice")
    _session(db, username="bob")

    db.set_action_env(alice_session, {"a": 1})
    db.set_action_env(alice_session, {"number_of_steps": 3})

    assert db.get_action_env("proj", "alice") == {"number_of_steps": 3}
    assert db.get_action_env("proj", "bob") == {}
    assert db.get_action_env("other-proj", "alice") == {}


def test_action_env_never_leaks_into_env_and_its_rows_never_show_up_in_get_signals(db):
    """The `env` and `action_env` columns never leak into each other, even
    for the same (user, project)."""
    session_id = _session(db)
    db.set_env(session_id, {"favorite_color": "blue"})
    db.set_action_env(session_id, {"number_of_steps": 1})

    assert db.get_env("proj", "user") == {"favorite_color": "blue"}
    assert db.get_action_env("proj", "user") == {"number_of_steps": 1}
    assert db.get_signals(session_id) == []


def test_set_action_env_writes_onto_the_given_session_never_the_latest_live_one(db):
    older = _session(db, start=datetime(2026, 1, 1))
    newer = _session(db, start=datetime(2026, 1, 2))
    assert db.get_latest_chat_session("user", "proj")["id"] == newer

    db.set_action_env(older, {"a": 1})

    assert Tracking.get(Tracking.action_env.is_null(False)).session_id == older


def test_resetting_a_project_wholesale_or_for_one_user_wipes_the_matching_action_env(db):
    alice_session = _session(db, username="alice")
    bob_session = _session(db, username="bob")
    db.set_action_env(alice_session, {"a": 1})
    db.set_action_env(bob_session, {"a": 1})

    db.reset_project_for_user("alice", "proj", type="live")
    assert db.get_action_env("proj", "alice") == {}
    assert db.get_action_env("proj", "bob") == {"a": 1}

    db.reset_project("proj")
    assert db.get_action_env("proj", "bob") == {}


class TestLinkToolEnvWritesToMessage:
    def test_binds_every_unlinked_tool_row_at_or_after_since_never_a_stale_one_from_before_it(self, db):
        # An orphan tool write from some earlier turn (never linked, for
        # whatever reason) must not be silently attributed to a later
        # turn's own assistant message just because it's still unlinked.
        session_id = _session(db)
        stale_row_id = db.set_action_env(session_id, {"old": "value"}, origin="tool")
        row_id = db.set_action_env(session_id, {"pnr": "ABC"}, origin="tool")
        since = Tracking.get_by_id(row_id).timestamp
        message_id = db.save_message("assistant", "Noted.", session_id)

        db.link_tool_env_writes_to_message(session_id, message_id, since=since)

        assert Tracking.get_by_id(row_id).message_id == message_id
        assert Tracking.get_by_id(stale_row_id).message_id is None

    def test_with_no_since_every_unlinked_row_is_bound(self, db):
        session_id = _session(db)
        row_id = db.set_action_env(session_id, {"pnr": "ABC"}, origin="tool")
        message_id = db.save_message("assistant", "Noted.", session_id)

        db.link_tool_env_writes_to_message(session_id, message_id)

        assert Tracking.get_by_id(row_id).message_id == message_id
