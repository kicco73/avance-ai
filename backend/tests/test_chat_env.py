"""Tests for tracking.env.Env/PersistedEnv — the per-(user, project)
store: the model's own free-form notes (`memory()`) and the automaton's
deterministic, action-set env keys (`action_set()`), each independently
persisted — memory rendered on its own for the prompt (memory_as_text),
the env through tracking.env_prompt_block with its own perimeter.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.env import Env, PersistedEnv

USERNAME = "user"
PROJECT_ID = "proj"


def _env(db, session_id: int = 0) -> PersistedEnv:
    # session_id is now required (PersistedEnv(None) raises) — every
    # no-arg call below is read-only, so a placeholder id is fine.
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
def test_update_drops_a_declared_key_the_automaton_owns(db):
    """A model echoing back a key the automaton declares must not
    duplicate into memory() on top of action_set() — whether or not an
    action's own `env:` field has actually set it yet (see Env.update's
    own declared_keys parameter)."""
    env = _env(db, _session(db))
    env.update_action_set({"WRONG_ANSWERS_ON_CURRENT_STEP": 2})

    env.update(
        {"WRONG_ANSWERS_ON_CURRENT_STEP": "2", "favorite_color": "blue"},
        declared_keys={"WRONG_ANSWERS_ON_CURRENT_STEP"},
    )

    assert db.get_env(PROJECT_ID, USERNAME) == {"favorite_color": "blue"}
    assert env.action_set() == {"WRONG_ANSWERS_ON_CURRENT_STEP": 2}


@pytest.mark.regression
def test_update_is_a_noop_when_every_key_is_filtered_out(db):
    env = _env(db, _session(db))

    env.update({"a": "1"}, declared_keys={"a"})

    assert db.get_env(PROJECT_ID, USERNAME) == {}


@pytest.mark.regression
def test_action_set_reads_a_value_set_via_update_action_set(db):
    env = _env(db, _session(db))
    env.update_action_set({"number_of_steps": 3})

    assert env.action_set() == {"number_of_steps": 3}
    assert env.memory() == {}


@pytest.mark.contract
def test_action_set_and_memory_are_independent_stores(db):
    env = _env(db, _session(db))
    env.update({"favorite_color": "blue"})
    env.update_action_set({"number_of_steps": 3})

    assert env.memory() == {"favorite_color": "blue"}
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
def test_memory_as_text_renders_memory_only(db):
    env = _env(db, _session(db))
    env.update({"favorite_color": "blue"})
    env.update_action_set({"number_of_steps": 3})

    result = env.memory_as_text()

    assert result == "favorite_color: blue"


@pytest.mark.contract
def test_update_action_set_returns_the_tracking_row_id_for_a_persisted_env_and_none_in_memory(db):
    env = _env(db, _session(db))

    row_id = env.update_action_set({"a": 1})

    assert isinstance(row_id, int)
    assert Env().update_action_set({"a": 1}) is None
    assert env.update_action_set({}) is None
