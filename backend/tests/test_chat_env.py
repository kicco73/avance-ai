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


def _live_env(db) -> PersistedEnv:
    return _env(db, _session(db))


@pytest.mark.regression
def test_update_merges_onto_stored_memory_overwriting_a_matching_key_and_ignoring_an_empty_write(db):
    env = _live_env(db)

    env.update({"a": "1"})
    env.update({"b": "2"})
    env.update({"a": "2"})
    env.update({})
    env.update(None)

    assert db.get_env(PROJECT_ID, USERNAME) == {"a": "2", "b": "2"}
    assert env.get("a") == "2"
    assert env.get("nope", "fallback") == "fallback"


@pytest.mark.contract
def test_update_drops_a_declared_key_the_automaton_owns_even_when_that_leaves_nothing_to_write(db):
    """A model echoing back a key the automaton declares must not
    duplicate into memory() on top of action_set() — whether or not an
    action's own `env:` field has actually set it yet (see Env.update's
    own declared_keys parameter)."""
    env = _live_env(db)
    env.update_action_set({"WRONG_ANSWERS_ON_CURRENT_STEP": 2})

    env.update(
        {"WRONG_ANSWERS_ON_CURRENT_STEP": "2", "favorite_color": "blue"},
        declared_keys={"WRONG_ANSWERS_ON_CURRENT_STEP"},
    )

    assert db.get_env(PROJECT_ID, USERNAME) == {"favorite_color": "blue"}
    assert env.action_set() == {"WRONG_ANSWERS_ON_CURRENT_STEP": 2}

    env.update({"favorite_color": "red"}, declared_keys={"favorite_color"})
    assert db.get_env(PROJECT_ID, USERNAME) == {"favorite_color": "blue"}


@pytest.mark.contract
def test_action_set_and_memory_are_independent_stores_both_readable_through_get(db):
    env = _live_env(db)

    env.update({"favorite_color": "blue"})
    env.update_action_set({"a": 1})
    env.update_action_set({"number_of_steps": 3})
    env.update_action_set({})
    env.update_action_set(None)

    assert env.memory() == {"favorite_color": "blue"}
    assert env.action_set() == {"a": 1, "number_of_steps": 3}
    assert env.get("number_of_steps") == 3
    # memory_as_text renders memory only, never the action set.
    assert env.memory_as_text() == "favorite_color: blue"


@pytest.mark.contract
def test_update_action_set_returns_the_tracking_row_id_for_a_persisted_env_and_none_in_memory_or_for_an_empty_write(db):
    env = _live_env(db)

    assert isinstance(env.update_action_set({"a": 1}), int)
    assert env.update_action_set({}) is None
    assert Env().update_action_set({"a": 1}) is None
