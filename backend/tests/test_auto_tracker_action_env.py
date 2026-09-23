"""Auto-tracking's end of the action-level `env` feature: once a trigger
fires an action, that action's `env` field is evaluated and merged onto
tracking.env.Env's persisted store, as seen through the env a caller can
read back (TurnService.get_env).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from system.web_session import WebSession
from tracking.env import PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a pytest fixture, used by name
pytestmark = pytest.mark.regression


def _automaton_with_env(trigger_expr: str, action_env: dict | None, target: str = "b") -> Automaton:
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target=target,
        trigger=trigger_expr, env=action_env,
    )
    state_a = State(input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(input_processor="ai", key="b", ui_label="B", final=target == "b", contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
        "a": state_a,
        "b": state_b,
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        general_attachments={},
        autotracking_on_ai_message=True,
        project_id=PROJECT_ID,
    )


class FakeSchemaAiService:
    def __init__(self, signals: dict) -> None:
        self._signals = signals

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        on_metadata("signals", self._signals)
        yield "Hi!"


async def _talking_in(turn_service_for, automaton: Automaton, signals: dict | None = None):
    turn_service = turn_service_for(automaton, ai_service=FakeSchemaAiService(signals or {"mySignal": 1}))
    db = turn_service_for.db
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    return turn_service, session["id"]


def _stored(turn_service, session_id: int) -> dict:
    """Everything a name could resolve to, whichever store holds it — the
    two are kept apart on purpose, so each test still names the one it
    means."""
    env = turn_service.get_env(session_id)
    return {**env["memory"], **env["action_set"]}


async def test_a_fired_actions_env_is_persisted(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for, _automaton_with_env("signal.mySignal >= 1", {"reset_counter": "True"}),
    )

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    env = turn_service.get_env(session_id)
    assert env["action_set"].get("reset_counter") in (True, "True")
    assert env["memory"] == {}


async def test_env_is_not_touched_when_the_trigger_does_not_fire(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for, _automaton_with_env("signal.mySignal >= 99", {"reset_counter": "True"}),
    )

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is False
    assert _stored(turn_service, session_id).get("reset_counter") is None


async def test_an_env_expression_can_self_reference_the_previous_stored_value(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for,
        _automaton_with_env("signal.mySignal >= 1", {"number_of_steps": "env.number_of_steps + 1"}, target="a"),
    )
    PersistedEnv(
        turn_service_for.db, FixedProjectContext(project_id=PROJECT_ID), session_id,
    ).update_action_set({"number_of_steps": 3})

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    assert turn_service.get_env(session_id)["action_set"]["number_of_steps"] == 4


async def test_self_referencing_an_env_key_that_was_never_stored_yet_leaves_it_unset(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for,
        _automaton_with_env("signal.mySignal >= 1", {"number_of_steps": "env.number_of_steps + 1"}, target="a"),
    )

    await turn_service.process_turn(session_id, "hello")

    assert _stored(turn_service, session_id).get("number_of_steps") is None


async def test_env_can_reference_a_signal_value_from_this_same_turn(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for, _automaton_with_env("signal.mySignal >= 1", {"last_signal": "signal.mySignal"}),
        {"mySignal": 7},
    )

    await turn_service.process_turn(session_id, "hello")

    assert _stored(turn_service, session_id)["last_signal"] == 7


async def test_an_action_with_no_env_field_never_touches_the_action_set_store(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for, _automaton_with_env("signal.mySignal >= 1", None),
    )

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    env = turn_service.get_env(session_id)
    assert env["action_set"] == {}
    assert env["memory"] == {}
