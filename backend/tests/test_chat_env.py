"""Tests for tracking.env.Env/PersistedEnv — the per-(user, project)
"environment" memory: free-form, model-reported values (`stored()`) and
deterministic, action-set values (`action_set()`), each independently
persisted and merged for prompt rendering.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv

USERNAME = "user"
PROJECT_ID = "proj"


def _env(db, session_id: int | None = None) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


def _session(db, username=USERNAME, project_id=PROJECT_ID, start=None):
    start = start or datetime(2026, 1, 1)
    db.ensure_project(project_id)
    db.publish_project(project_id)
    return db.create_chat_session(
        username=username, project_id=project_id,
        revision=db.get_project_published_revision(project_id),
        datetime_start=start, datetime_end=start,
        start_state="a", end_state="a",
    )


@pytest.mark.regression
def test_get_reads_a_stored_value(db):
    session_id = _session(db)
    db.set_env(session_id, {"favorite_color": "blue"})

    assert _env(db).get("favorite_color") == "blue"


@pytest.mark.regression
def test_get_falls_back_to_default_for_an_unknown_key(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    assert _env(db).get("nope", "fallback") == "fallback"


@pytest.mark.regression
def test_update_merges_onto_existing_stored_values(db):
    env = _env(db, _session(db))
    env.update({"a": "1"})

    env.update({"b": "2"})

    assert db.get_env(PROJECT_ID, USERNAME) == {"a": "1", "b": "2"}


@pytest.mark.regression
def test_update_overwrites_a_matching_key(db):
    env = _env(db, _session(db))
    env.update({"a": "1"})

    env.update({"a": "2"})

    assert db.get_env(PROJECT_ID, USERNAME) == {"a": "2"}


@pytest.mark.regression
def test_update_with_empty_values_is_a_noop(db):
    env = _env(db, _session(db))
    env.update({"a": "1"})

    env.update({})
    env.update(None)

    assert db.get_env(PROJECT_ID, USERNAME) == {"a": "1"}


@pytest.mark.contract
def test_update_drops_a_key_that_is_currently_action_set(db):
    """A model echoing back a key an action's own `env:` field already
    set must not duplicate into stored() on top of action_set()."""
    env = _env(db, _session(db))
    env.update_action_set({"WRONG_ANSWERS_ON_CURRENT_STEP": 2})

    env.update({"WRONG_ANSWERS_ON_CURRENT_STEP": "2", "favorite_color": "blue"})

    assert db.get_env(PROJECT_ID, USERNAME) == {"favorite_color": "blue"}
    assert env.action_set() == {"WRONG_ANSWERS_ON_CURRENT_STEP": 2}


@pytest.mark.regression
def test_update_is_a_noop_when_every_key_is_filtered_out(db):
    env = _env(db, _session(db))
    env.update_action_set({"a": 1})

    env.update({"a": "1"})

    assert db.get_env(PROJECT_ID, USERNAME) == {}


@pytest.mark.regression
def test_action_set_reads_a_value_set_via_update_action_set(db):
    env = _env(db, _session(db))
    env.update_action_set({"number_of_steps": 3})

    assert env.action_set() == {"number_of_steps": 3}
    assert env.stored() == {}


@pytest.mark.contract
def test_action_set_and_stored_are_independent_stores(db):
    env = _env(db, _session(db))
    env.update({"favorite_color": "blue"})
    env.update_action_set({"number_of_steps": 3})

    assert env.stored() == {"favorite_color": "blue"}
    assert env.action_set() == {"number_of_steps": 3}


@pytest.mark.regression
def test_update_action_set_merges_onto_existing_action_set_values(db):
    env = _env(db, _session(db))
    env.update_action_set({"a": 1})

    env.update_action_set({"b": 2})

    assert env.action_set() == {"a": 1, "b": 2}


@pytest.mark.regression
def test_update_action_set_with_empty_values_is_a_noop(db):
    env = _env(db, _session(db))
    env.update_action_set({"a": 1})

    env.update_action_set({})
    env.update_action_set(None)

    assert env.action_set() == {"a": 1}


@pytest.mark.contract
def test_get_reads_an_action_set_value_too(db):
    env = _env(db, _session(db))
    env.update_action_set({"number_of_steps": 3})

    assert env.get("number_of_steps") == 3


@pytest.mark.contract
def test_serialise_as_text_merges_stored_and_action_set(db):
    env = _env(db, _session(db))
    env.update({"favorite_color": "blue"})
    env.update_action_set({"number_of_steps": 3})

    result = env.serialise_as_text()

    assert "favorite_color: blue" in result
    assert "number_of_steps: 3" in result
