"""set_auto_tracking_enabled/is_auto_tracking_enabled — the "Dev mode:
freeze automatic state transitions" toggle, per 'test' session; a native
session can never be frozen. Signal evaluation is never gated by this —
only whether a triggered action gets applied.
"""
from __future__ import annotations

import json

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from system.web_session import WebSession
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression


def _automaton(trigger_expr: str) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger_expr)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        general_attachments={},
        autotracking_on_ai_message=True,
        project_id=PROJECT_ID,
    )


class FakeSchemaAiService:
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


def _turn_service(turn_service_for, trigger_expr: str = "signal.mySignal >= 1", signals_json: str = '{"mySignal": 1}'):
    automaton = _automaton(trigger_expr)
    turn_service = turn_service_for(automaton, ai_service=FakeSchemaAiService(signals_json))
    db = turn_service_for.db
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    return turn_service


async def test_a_matching_trigger_fires_when_auto_tracking_is_enabled(turn_service_for):
    turn_service = _turn_service(turn_service_for)
    session = await turn_service.enter_session(PROJECT_ID, 'test')

    result = await turn_service.process_turn(session["id"], "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_matching_trigger_does_not_fire_when_auto_tracking_is_frozen(turn_service_for):
    turn_service = _turn_service(turn_service_for)
    session = await turn_service.enter_session(PROJECT_ID, 'test')
    turn_service.set_auto_tracking_enabled(session["id"], False)

    result = await turn_service.process_turn(session["id"], "hello")

    assert result["state_changed"] is False
    assert result["new_state"] is None


async def test_a_frozen_session_still_offers_the_button_the_trigger_would_have_pressed(turn_service_for):
    """The other half of freezing: the transition no longer happens on its
    own, so the person has to be able to make it happen — the triggered
    action becomes a button the state actually shows."""
    turn_service = _turn_service(turn_service_for)
    session = await turn_service.enter_session(PROJECT_ID, 'test')
    turn_service.set_auto_tracking_enabled(session["id"], False)

    result = await turn_service.process_turn(session["id"], "hello")

    assert [button["name"] for button in result["buttons"]] == ["advance"]


async def test_signals_are_still_computed_and_logged_while_frozen(turn_service_for):
    """The whole point: freezing the *transition* must never also freeze
    signal computation — the Signals tab still needs something to show."""
    turn_service = _turn_service(turn_service_for)
    session = await turn_service.enter_session(PROJECT_ID, 'test')
    turn_service.set_auto_tracking_enabled(session["id"], False)

    await turn_service.process_turn(session["id"], "hello")

    logged = turn_service.get_session_signals(session["id"])
    assert len(logged) == 1
    assert json.loads(logged[0]["values"])["mySignal"] == 1


async def test_freezing_a_live_session_has_no_effect(turn_service_for):
    """Auto-tracking freeze only ever applies to 'test' sessions — a
    live session's trigger still fires normally even if
    set_auto_tracking_enabled(session_id, False) was called for it."""
    turn_service = _turn_service(turn_service_for)
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    turn_service.set_auto_tracking_enabled(session["id"], False)

    result = await turn_service.process_turn(session["id"], "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_freezing_one_test_session_never_affects_another(turn_service_for):
    """Not global: freezing session A must never freeze session B, even
    though both are 'test' sessions of the same project."""
    turn_service = _turn_service(turn_service_for)
    frozen = await turn_service.create_session_of(PROJECT_ID, 'test')
    other = await turn_service.create_session_of(PROJECT_ID, 'test')
    turn_service.set_auto_tracking_enabled(frozen["id"], False)

    result = await turn_service.process_turn(other["id"], "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"
    assert turn_service.is_auto_tracking_enabled(frozen["id"]) is False
