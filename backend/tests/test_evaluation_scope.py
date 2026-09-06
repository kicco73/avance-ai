"""Tests for tracking.evaluation_scope.EvaluationScopeBuilder — the one
place a trigger/`env:`-expression evaluation scope gets assembled: the
`signal`/`env`/`session` namespaces plus any referenced core metric.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, MemoryArchive, Source, State
from metrics.metric_service import MetricService
from tracking.actuators import AttachmentNamespace
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
    # Read-only here (session_id is now required — PersistedEnv(None)
    # raises — and no test in this file writes through this instance).
    env = PersistedEnv(db, project_service, session_id=0)
    metrics = MetricService(db, project_service)
    return EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_service), UserFacts(db), db)


def _automaton_with_trigger(
    trigger_expr: str, attachments: dict[str, MemoryArchive] | None = None, env: dict | None = None,
    sources: list[Source] | None = None,
) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger_expr, env=env)
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
        project_id=PROJECT_ID,
        sources=sources,
    )


def _published(db) -> None:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)


def _session(db) -> int:
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )


def _pinned(db, automaton: Automaton) -> Automaton:
    automaton.project_id = PROJECT_ID
    automaton.set_storage_location(db.get_project_revision(PROJECT_ID))
    return automaton


def test_the_scope_always_carries_every_namespace(db):
    _published(db)

    scope = _builder(db).build(_automaton_with_trigger("signal.mood >= 1"), "a", {})

    assert set(scope["signal"]) == set()
    assert scope["env"] == {}
    assert scope["session"].number_of_user_sessions() == 0
    assert scope["session"].metric.engagement() is not None
    assert scope["user"]["email"] == USERNAME
    assert isinstance(scope["source"], SourceNamespace)
    assert isinstance(scope["attachment"], AttachmentNamespace)
    assert scope["metric"].retention() is not None


@pytest.mark.parametrize("trigger", [
    "session.metric.state_stability() >= 50",
    "metric.retention() >= 0",
], ids=["session-metric", "metric"])
def test_a_metric_namespace_is_usable_in_a_trigger_end_to_end(db, trigger):
    # No transitions on record yet — state_stability starts at 100.
    _published(db)
    automaton = _automaton_with_trigger(trigger)

    scope = _builder(db).build(automaton, "a", {})

    assert automaton.evaluate_triggers("a", scope) == "advance"


def test_a_session_fact_a_user_fact_and_an_action_set_env_value_are_each_usable_in_a_trigger_end_to_end(db):
    _published(db)
    builder = _builder(db)

    sessions = _automaton_with_trigger("session.number_of_user_sessions() >= 1")
    assert sessions.evaluate_triggers("a", builder.build(sessions, "a", {})) is None
    session_id = _session(db)
    assert sessions.evaluate_triggers("a", builder.build(sessions, "a", {})) == "advance"

    db.set_user_role(USERNAME, "admin")
    user = _automaton_with_trigger("user.role == 'admin'")
    scope = builder.build(user, "a", {})
    assert scope["user"]["role"] == "admin"
    assert user.evaluate_triggers("a", scope) == "advance"

    db.set_action_env(session_id, {"number_of_steps": 3})
    env_trigger = _automaton_with_trigger("env.number_of_steps >= 3")
    scope = builder.build(env_trigger, "a", {})
    assert scope["env"]["number_of_steps"] == 3
    assert env_trigger.evaluate_triggers("a", scope) == "advance"


def test_the_env_namespace_carries_the_action_set_only_never_the_models_own_memory(db):
    """Only action_set() feeds the `env` namespace — the model's own
    free-form memory() notes must never leak into it, nor anywhere else
    a script or trigger can reach: `env` is the one Env-backed name in
    the scope."""
    _published(db)
    db.set_env(_session(db), {"favorite_color": "blue"})

    scope = _builder(db).build(_automaton_with_trigger("signal.mood >= 1"), "a", {})

    assert "favorite_color" not in scope["env"]
    assert "memory" not in scope
    assert not any(
        isinstance(value, dict) and "favorite_color" in value for key, value in scope.items() if key != "env"
    )


def test_a_declared_source_is_readable_from_an_env_expression_end_to_end(db):
    """source.<name>.select_rows_containing(...) reads straight from Db,
    at the same (project_name, revision) the automaton itself was loaded
    from (see Automaton.set_storage_location) — never automaton.
    attachments' in-memory copy, which is why this seeds the file through
    save_project_files rather than constructing a MemoryArchive."""
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"notes.txt": b"note\nhello from the archive\n"}, {"notes.txt": "text/plain"})
    automaton = _pinned(db, _automaton_with_trigger(
        "signal.mood >= 1",
        env={"notes": "source.pino.select_rows_containing('hello')"},
        sources=[Source(name="pino", url="avance:notes.txt", ui_label="pino")],
    ))
    action = automaton.states["a"].actions[0]

    scope = _builder(db).build(automaton, "a", {})

    assert scope["source"].pino.select_rows_containing("hello") == "note\nhello from the archive\n"
    assert Automaton.eval_action_env(action, scope) == {"notes": "note\nhello from the archive\n"}


def test_attachment_read_resolves_a_text_archive_from_both_scope_views_and_raises_for_anything_else(db):
    """attachment.read(name), like a source read, goes straight to Db at
    the automaton's own pinned (project_id, revision) — never automaton.
    attachments' in-memory copy — and stays present in the actuator view
    an on-enter line actually runs against, which drops only `session`."""
    db.ensure_project(PROJECT_ID)
    db.save_project_files(
        PROJECT_ID, {"behaviour/policy.txt": b"be kind", "logo.png": b"\x89PNG"},
        {"behaviour/policy.txt": "text/plain", "logo.png": "image/png"},
    )
    automaton = _pinned(db, _automaton_with_trigger("signal.mood >= 1"))

    scope = _builder(db).build(automaton, "a", {})
    actuator_scope = scope.for_actuators()

    assert scope["attachment"].read("policy.txt") == "be kind"
    assert actuator_scope["attachment"].read("policy.txt") == "be kind"
    with pytest.raises(ValueError, match="not found"):
        scope["attachment"].read("nope.txt")
    with pytest.raises(ValueError, match="binary file"):
        scope["attachment"].read("logo.png")

    assert "session" in scope
    assert "session" not in actuator_scope
    assert set(actuator_scope) == set(scope) - {"session"}
    assert actuator_scope.automaton is automaton and actuator_scope.state_key == "a"
