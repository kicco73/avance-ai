"""Tests for tracking.evaluation_scope.EvaluationScopeBuilder — the one
place a trigger/`env:`-expression evaluation scope gets assembled: the
`signal`/`env`/`session` namespaces plus any referenced core metric.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, MemoryArchive, Source, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.sources import SourceNamespace
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


def _builder(db) -> EvaluationScopeBuilder:
    project_service = FixedProjectContext(project_id=PROJECT_ID)
    env = PersistedEnv(db, project_service)
    metrics = MetricService(db, project_service)
    return EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_service), UserFacts(db), db)


def _automaton_with_trigger(trigger_expr: str, attachments: dict[str, MemoryArchive] | None = None) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger_expr)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments=attachments or {},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def test_scope_always_includes_every_namespace(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    automaton = _automaton_with_trigger("signal.mood >= 1")

    scope = _builder(db).build(automaton, "a", {})

    assert set(scope["signal"]) == set()
    assert scope["env"] == {}
    assert scope["session"].number_of_user_sessions() == 0
    assert scope["session"].metric.engagement() is not None
    assert scope["user"]["email"] == USERNAME
    assert isinstance(scope["source"], SourceNamespace)
    assert scope["metric"].retention() is not None


def test_session_metric_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    automaton = _automaton_with_trigger("session.metric.state_stability() >= 50")

    scope = _builder(db).build(automaton, "a", {})

    # No transitions on record yet — state_stability starts at 100.
    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_metric_namespace_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    automaton = _automaton_with_trigger("metric.retention() >= 0")

    scope = _builder(db).build(automaton, "a", {})

    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_session_fact_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    automaton = _automaton_with_trigger("session.number_of_user_sessions() >= 1")
    builder = _builder(db)

    # No sessions yet — trigger shouldn't fire.
    scope = builder.build(automaton, "a", {})
    assert automaton.evaluate_triggers("a", scope) is None

    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    scope = builder.build(automaton, "a", {})
    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_user_fact_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_user_role(USERNAME, "admin")
    automaton = _automaton_with_trigger("user.role == 'admin'")

    scope = _builder(db).build(automaton, "a", {})

    assert scope["user"]["role"] == "admin"
    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_declared_source_is_usable_in_an_env_expression_end_to_end(db):
    """source.<name>.read() reads straight from Db, at the same
    (project_name, revision) the automaton itself was loaded from (see
    Automaton.set_storage_location) — never automaton.attachments'
    in-memory copy, which is why this seeds the file through
    save_project_files rather than constructing a MemoryArchive."""
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"notes.txt": b"hello from the archive"}, {"notes.txt": "text/plain"})
    revision = db.get_project_revision(PROJECT_ID)
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="b",
        trigger="signal.mood >= 1", env={"notes": "source.pino.read()"},
    )
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    automaton = Automaton(
        init_action=Action(name="init_action", ui_label="init_action", ui_button="", target="a"),
        states={"": State(key="", ui_label="", final=False, actions=[]), "a": state_a},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, project_id=PROJECT_ID,
        sources=[Source(name="pino", url="avance:notes.txt", ui_label="pino")],
    )
    automaton.set_storage_location(revision)

    scope = _builder(db).build(automaton, "a", {})

    assert scope["source"].pino.read() == "hello from the archive"
    assert Automaton.eval_action_env(action, scope) == {"notes": "hello from the archive"}


def test_env_action_set_value_is_usable_in_a_trigger_end_to_end(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_action_env(PROJECT_ID, {"number_of_steps": 3}, USERNAME)
    automaton = _automaton_with_trigger("env.number_of_steps >= 3")

    scope = _builder(db).build(automaton, "a", {})

    assert scope["env"]["number_of_steps"] == 3
    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_env_namespace_excludes_free_form_stored_values(db):
    """Only action_set() feeds the `env` namespace — a model-reported
    free-form stored() value must never leak into it."""
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_env(PROJECT_ID, {"favorite_color": "blue"}, USERNAME)
    automaton = _automaton_with_trigger("signal.mood >= 1")

    scope = _builder(db).build(automaton, "a", {})

    assert "favorite_color" not in scope["env"]


def test_the_actuator_view_of_a_scope_has_no_session(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    automaton = _automaton_with_trigger("signal.mood >= 1")

    scope = _builder(db).build(automaton, "a", {})
    actuator_scope = scope.for_actuators()

    assert "session" in scope
    assert "session" not in actuator_scope
    assert set(actuator_scope) == set(scope) - {"session"}
    assert actuator_scope.automaton is automaton and actuator_scope.state_key == "a"
