"""Tests for tracking.evaluation_scope.EvaluationScopeBuilder — the one
place a trigger/`env:`-expression evaluation scope gets assembled: the
`signal`/`env`/`system`/`session` namespaces plus any referenced core
metric. Moved (and adapted to the new namespaced syntax) from tests that
used to exercise the same end-to-end concerns through tracking.env.Env's
own merge_if_referenced, before this class existed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_NAME = "proj"


def _builder(db) -> EvaluationScopeBuilder:
    get_username = lambda: USERNAME
    get_active_project_name = lambda: PROJECT_NAME
    env = PersistedEnv(db, get_username=get_username, get_active_project_name=get_active_project_name)
    metrics = MetricService(db, get_username=get_username, get_active_project_name=get_active_project_name)
    return EvaluationScopeBuilder(env, metrics, SystemFacts(), SessionFacts(db, get_username, get_active_project_name))


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
        autotracking_on_ai_message=False,
    )


def test_scope_always_includes_all_four_namespaces(db):
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    automaton = _automaton_with_trigger("signal.mood >= 1")

    scope = _builder(db).build(automaton, "a", {})

    assert set(scope["signal"]) == set()
    assert scope["env"] == {}
    assert scope["system"].today().count("-") == 2
    assert scope["session"].number_of_user_sessions() == 0


def test_session_fact_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    automaton = _automaton_with_trigger("session.number_of_user_sessions() >= 1")
    builder = _builder(db)

    # No sessions yet — trigger shouldn't fire.
    scope = builder.build(automaton, "a", {})
    assert automaton.evaluate_triggers("a", scope) is None

    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    scope = builder.build(automaton, "a", {})
    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_env_action_set_value_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_action_env(PROJECT_NAME, {"number_of_steps": 3}, USERNAME)
    automaton = _automaton_with_trigger("env.number_of_steps >= 3")

    scope = _builder(db).build(automaton, "a", {})

    assert scope["env"]["number_of_steps"] == 3
    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_env_namespace_excludes_free_form_stored_values(db):
    """Only action_set() feeds the `env` namespace — a model-reported
    free-form stored() value (see tracking.env.Env's own docstring) must
    never leak into it, unlike before this refactor (see Env.to_dict's
    own three-way merge, now gone)."""
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_env(PROJECT_NAME, {"favorite_color": "blue"}, USERNAME)
    automaton = _automaton_with_trigger("signal.mood >= 1")

    scope = _builder(db).build(automaton, "a", {})

    assert "favorite_color" not in scope["env"]
