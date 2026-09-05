"""Auto-tracking's end of the action-level `env` feature: once a trigger
fires an action, that action's `env` field is evaluated and merged onto
tracking.env.Env's persisted store, driven through TrackingService.process()
in autotracking_on_ai_message mode.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from metrics.metric_service import MetricService
from conftest import make_test_actuator_factory
from tracking.tracking_service import TrackingService

USERNAME = "user"
PROJECT_ID = "proj"

# Each test verifies one fact about action-level env: persisted on fire,
# untouched otherwise, self-referencing, scoped to this turn's signals.
pytestmark = pytest.mark.regression


def _automaton_with_env(trigger_expr: str, action_env: dict | None, target: str = "b") -> Automaton:
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target=target,
        trigger=trigger_expr, env=action_env,
    )
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=target == "b", contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": state_a,
        "b": state_b,
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=True,
    )


class FakeProjectService:
    """Stands in for ProjectService — just enough for TrackingService to
    run a real turn against a fixed, hand-built automaton, no file/YAML
    involved."""

    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_project_availability(self, project_id: str):
        return (False, None)


class FakeSchemaAiService:
    """A v2 (schema)-shaped fake — reports `signals` straight through
    on_metadata as a raw JSON string, independent of any tag-scanning."""

    def __init__(self, signals_json: str) -> None:
        self._signals_json = signals_json

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        on_metadata("signals", self._signals_json)
        yield "Hi!"


def _tracking_service(db, automaton: Automaton, signals_json: str = '{"mySignal": 1}') -> tuple[TrackingService, FakeSchemaAiService]:
    ai_service = FakeSchemaAiService(signals_json)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, FixedProjectContext(project_id=PROJECT_ID))
    return TrackingService(db, project_service, metrics, make_test_actuator_factory(db)), ai_service


def _session_id(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )


def _env(db, session_id: int | None = None) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


async def test_a_fired_actions_env_is_persisted(db):
    automaton = _automaton_with_env("signal.mySignal >= 1", {"reset_counter": "True"})
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is True
    env = _env(db)
    # Lands in the action-set store, never the model's own memory() one.
    # Accept bool or string since either is a valid evaluated representation.
    assert env.action_set().get("reset_counter") in (True, "True")
    assert env.memory() == {}


async def test_env_is_not_touched_when_the_trigger_does_not_fire(db):
    automaton = _automaton_with_env("signal.mySignal >= 99", {"reset_counter": "True"})
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is False
    env = _env(db)
    assert env.get("reset_counter") is None


async def test_an_env_expression_can_self_reference_the_previous_stored_value(db):
    automaton = _automaton_with_env("signal.mySignal >= 1", {"number_of_steps": "env.number_of_steps + 1"}, target="a")
    session_id = _session_id(db)
    env = _env(db, session_id)
    # Seeded directly in the action-set store as a real int, since that's
    # what simpleeval produces (unlike a model-reported string value).
    env.update_action_set({"number_of_steps": 3})
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is True  # a self-loop (target == "a") still counts as fired
    assert env.action_set()["number_of_steps"] == 4


async def test_self_referencing_an_env_key_that_was_never_stored_yet_leaves_it_unset(db):
    automaton = _automaton_with_env("signal.mySignal >= 1", {"number_of_steps": "env.number_of_steps + 1"}, target="a")
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')

    await service._process(session_id, "hello", ai_service)

    env = _env(db)
    assert env.get("number_of_steps") is None


async def test_env_can_reference_a_signal_value_from_this_same_turn(db):
    automaton = _automaton_with_env("signal.mySignal >= 1", {"last_signal": "signal.mySignal"})
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 7}')

    await service._process(session_id, "hello", ai_service)

    env = _env(db)
    assert env.get("last_signal") == 7


async def test_an_action_with_no_env_field_never_touches_the_action_set_store(db):
    automaton = _automaton_with_env("signal.mySignal >= 1", None)
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is True
    env = _env(db)
    assert env.action_set() == {}
    assert env.memory() == {}
