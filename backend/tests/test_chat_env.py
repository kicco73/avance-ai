"""Tests for chat.env.Env — the per-(user, project) "environment"
memory: free-form values persisted via db.Db.get_env/set_env (a dedicated
env-only row on the Tracking event log — see its own docstring), enriched
on every read with a fixed set of always-computed keys (see
automaton_builder.ENV_COMPUTED_KEYS) that are never persisted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from automaton.automaton import Action, Automaton, State
from tracking.env import Env

# tracking/env.py is unchanged in shape by this refactor — every method
# used below (get/update/update_action_set/action_set/stored/
# clear_action_set/computed/to_dict/merge_if_referenced) matches the
# current source exactly. Classified per-test below: a structural/
# precedence guarantee between the stored/action_set/computed layers is
# `contract`, a specific computed value or one-off edge case is
# `regression`.

USERNAME = "user"
PROJECT_NAME = "proj"


def _env(db) -> Env:
    return Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)


def _session(db, username=USERNAME, project_name=PROJECT_NAME, start=None):
    # set_env resolves onto the (user, project)'s latest chat session
    # (see db.Db.set_env) — a no-op without one, so any test that stores
    # a value (directly or via env.update) needs a session first.
    start = start or datetime(2026, 1, 1)
    return db.create_chat_session(
        username=username, project_name=project_name,
        datetime_start=start, datetime_end=start,
        start_state="a", end_state="a",
    )


def _automaton_with_trigger(trigger_expr: str) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger_expr)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=True,
        autotracking_on_ai_message=False,
    )


@pytest.mark.regression
def test_get_reads_a_stored_value(db):
    _session(db)
    db.set_env(PROJECT_NAME, {"favorite_color": "blue"}, USERNAME)

    assert _env(db).get("favorite_color") == "blue"


@pytest.mark.regression
def test_get_falls_back_to_default_for_an_unknown_key(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    assert _env(db).get("nope", "fallback") == "fallback"


@pytest.mark.regression
def test_update_merges_onto_existing_stored_values(db):
    _session(db)
    env = _env(db)
    env.update({"a": "1"})

    env.update({"b": "2"})

    assert db.get_env(PROJECT_NAME, USERNAME) == {"a": "1", "b": "2"}


@pytest.mark.regression
def test_update_overwrites_a_matching_key(db):
    _session(db)
    env = _env(db)
    env.update({"a": "1"})

    env.update({"a": "2"})

    assert db.get_env(PROJECT_NAME, USERNAME) == {"a": "2"}


@pytest.mark.regression
def test_update_with_empty_values_is_a_noop(db):
    _session(db)
    env = _env(db)
    env.update({"a": "1"})

    env.update({})
    env.update(None)

    assert db.get_env(PROJECT_NAME, USERNAME) == {"a": "1"}


@pytest.mark.contract
def test_update_drops_a_computed_key_the_model_echoed_back(db):
    """A model shown `today: 2026-01-01` in its own prompt (see
    MetadataHandler.build_prompt/env.to_dict) will sometimes echo it back
    in its own [env] tag despite being told only to report new/changed
    values — that must never pollute stored()/the Inspector's "AI"
    section with what is, and must remain, a purely computed value."""
    _session(db)
    env = _env(db)

    env.update({"today": "2026-01-01", "favorite_color": "blue"})

    assert db.get_env(PROJECT_NAME, USERNAME) == {"favorite_color": "blue"}


@pytest.mark.contract
def test_update_drops_a_key_that_is_currently_action_set(db):
    """Same bug, different source: an action's own `env:` field (see
    automaton_builder.py's _build_action) sets a key like
    WRONG_ANSWERS_ON_CURRENT_STEP, the model sees it in its own prompt
    and echoes it back — must not duplicate into stored() ("AI") on top
    of action_set() ("SET"), see chat_service.py's/auto_tracker.py's own
    _apply_action_env for the write path that owns this key instead."""
    _session(db)
    env = _env(db)
    env.update_action_set({"WRONG_ANSWERS_ON_CURRENT_STEP": 2})

    env.update({"WRONG_ANSWERS_ON_CURRENT_STEP": "2", "favorite_color": "blue"})

    assert db.get_env(PROJECT_NAME, USERNAME) == {"favorite_color": "blue"}
    assert env.action_set() == {"WRONG_ANSWERS_ON_CURRENT_STEP": 2}


@pytest.mark.regression
def test_update_is_a_noop_when_every_key_is_filtered_out(db):
    _session(db)
    env = _env(db)
    env.update_action_set({"a": 1})

    env.update({"a": "1", "today": "2026-01-01"})

    assert db.get_env(PROJECT_NAME, USERNAME) == {}


@pytest.mark.regression
def test_clear_action_set_wipes_every_action_set_key(db):
    _session(db)
    env = _env(db)
    env.update_action_set({"a": 1, "b": 2})

    env.clear_action_set()

    assert env.action_set() == {}


@pytest.mark.contract
def test_clear_action_set_leaves_stored_and_computed_untouched(db):
    _session(db)
    env = _env(db)
    env.update({"favorite_color": "blue"})
    env.update_action_set({"a": 1})

    env.clear_action_set()

    assert env.stored() == {"favorite_color": "blue"}
    assert "today" in env.computed()


@pytest.mark.contract
def test_today_and_time_are_computed_fresh_never_stored(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    env = _env(db)

    result = env.to_dict()

    assert result["today"].count("-") == 2  # ISO date, YYYY-MM-DD
    assert result["time"].count(":") == 2  # HH:MM:SS
    assert "today" not in db.get_env(PROJECT_NAME, USERNAME)
    assert "time" not in db.get_env(PROJECT_NAME, USERNAME)


@pytest.mark.regression
def test_number_of_user_sessions_counts_every_session_for_the_project(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    env = _env(db)
    assert env.get("number_of_user_sessions") == 0

    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    assert env.get("number_of_user_sessions") == 1

    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 2), datetime_end=datetime(2026, 1, 2),
        start_state="a", end_state="a",
    )
    assert env.get("number_of_user_sessions") == 2


@pytest.mark.regression
def test_current_session_duration_in_minutes_is_zero_with_no_session(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    assert _env(db).get("current_session_duration_in_minutes") == 0.0


@pytest.mark.regression
def test_current_session_duration_in_minutes_uses_the_most_recent_session(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    ten_minutes_ago = datetime.utcnow() - timedelta(minutes=10)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=ten_minutes_ago, datetime_end=ten_minutes_ago,
        start_state="a", end_state="a",
    )

    duration = _env(db).get("current_session_duration_in_minutes")

    assert 9.5 <= duration <= 10.5


@pytest.mark.regression
def test_last_user_session_datetime_is_none_for_a_first_ever_session(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )

    assert _env(db).get("last_user_session_datetime") is None


@pytest.mark.regression
def test_last_user_session_datetime_is_the_previous_sessions_start(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1, 9, 0), datetime_end=datetime(2026, 1, 1, 9, 30),
        start_state="a", end_state="a",
    )
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 2, 9, 0), datetime_end=datetime(2026, 1, 2, 9, 30),
        start_state="a", end_state="a",
    )

    last_session_datetime = _env(db).get("last_user_session_datetime")
    assert last_session_datetime is not None

    if isinstance(last_session_datetime, str):
        last_session_datetime = datetime.fromisoformat(last_session_datetime.replace("Z", "+00:00"))

    if last_session_datetime.tzinfo is not None:
        last_session_datetime = last_session_datetime.astimezone(timezone.utc).replace(tzinfo=None)

    assert last_session_datetime == datetime(2026, 1, 1, 9, 0)


@pytest.mark.regression
def test_state_duration_in_minutes_is_zero_with_no_transition_yet(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    assert _env(db).get("state_duration_in_minutes") == 0.0


@pytest.mark.regression
def test_state_duration_in_minutes_since_the_last_real_transition(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="b",
    )
    thirty_minutes_ago = datetime.utcnow() - timedelta(minutes=30)
    db.save_transition("a", "advance", "b", session_id, transition_log_level="WARNING")
    # save_transition always timestamps "now" — backdate it directly to
    # make the duration deterministic for the assertion below.
    from db.models import Tracking as TrackingModel
    TrackingModel.update(timestamp=thirty_minutes_ago).where(TrackingModel.session == session_id).execute()

    duration = _env(db).get("state_duration_in_minutes")

    assert 29.5 <= duration <= 30.5


@pytest.mark.contract
def test_env_computed_key_is_usable_in_a_trigger(db):
    db.set_active_project_name(PROJECT_NAME, USERNAME)
    automaton = _automaton_with_trigger("number_of_user_sessions >= 1")
    env = _env(db)

    # No sessions yet — not referenced-check bypassed, merge should be a
    # no-op result-wise (key present but 0 doesn't satisfy the trigger).
    names = env.merge_if_referenced(automaton, "a", {})
    assert names.get("number_of_user_sessions") == 0

    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    names = env.merge_if_referenced(automaton, "a", {})
    assert names["number_of_user_sessions"] == 1
    assert automaton.evaluate_triggers("a", names) == "advance"


@pytest.mark.regression
def test_action_set_reads_a_value_set_via_update_action_set(db):
    _session(db)
    env = _env(db)
    env.update_action_set({"number_of_steps": 3})

    assert env.action_set() == {"number_of_steps": 3}
    assert env.stored() == {}


@pytest.mark.contract
def test_action_set_and_stored_are_independent_stores(db):
    _session(db)
    env = _env(db)
    env.update({"favorite_color": "blue"})
    env.update_action_set({"number_of_steps": 3})

    assert env.stored() == {"favorite_color": "blue"}
    assert env.action_set() == {"number_of_steps": 3}


@pytest.mark.regression
def test_update_action_set_merges_onto_existing_action_set_values(db):
    _session(db)
    env = _env(db)
    env.update_action_set({"a": 1})

    env.update_action_set({"b": 2})

    assert env.action_set() == {"a": 1, "b": 2}


@pytest.mark.regression
def test_update_action_set_with_empty_values_is_a_noop(db):
    _session(db)
    env = _env(db)
    env.update_action_set({"a": 1})

    env.update_action_set({})
    env.update_action_set(None)

    assert env.action_set() == {"a": 1}


@pytest.mark.contract
def test_get_reads_an_action_set_value_too(db):
    _session(db)
    env = _env(db)
    env.update_action_set({"number_of_steps": 3})

    assert env.get("number_of_steps") == 3


@pytest.mark.contract
def test_to_dict_merges_stored_and_action_set_and_computed(db):
    _session(db)
    env = _env(db)
    env.update({"favorite_color": "blue"})
    env.update_action_set({"number_of_steps": 3})

    result = env.to_dict()

    assert result["favorite_color"] == "blue"
    assert result["number_of_steps"] == 3
    assert "today" in result


@pytest.mark.contract
def test_merge_if_referenced_includes_action_set_values(db):
    _session(db)
    automaton = _automaton_with_trigger("number_of_steps >= 3")
    env = _env(db)
    env.update_action_set({"number_of_steps": 3})

    names = env.merge_if_referenced(automaton, "a", {})

    assert names["number_of_steps"] == 3
    assert automaton.evaluate_triggers("a", names) == "advance"


@pytest.mark.regression
def test_merge_if_referenced_is_a_noop_when_no_trigger_mentions_env(db):
    _session(db)
    automaton = _automaton_with_trigger("mySignal >= 1")
    env = _env(db)
    env.update({"a": "1"})

    names = env.merge_if_referenced(automaton, "a", {"mySignal": 1})

    assert names == {"mySignal": 1}
