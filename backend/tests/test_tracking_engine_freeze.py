"""TrackingService.auto_tracking_enabled — EditProjectView.vue's own "Dev
mode: freeze automatic state transitions" toggle. Signal evaluation
itself is never gated by this (the AI still computes/reports signal
values on every turn, same prompt either way) — only whether a
triggered action actually gets *selected* and applied (see
TrackingEngine.evaluate_triggered_action). Frozen or not, the signal
values a turn actually saw are always persisted (see TrackingEngine.
apply_transition's own action-is-None branch) so the Signals tab still
has something to show even while nothing is moving.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

USERNAME = "user"
PROJECT_NAME = "proj"

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
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=True,
    )


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME


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


def _tracking_service(db, automaton: Automaton, signals_json: str = '{"mySignal": 1}') -> TrackingService:
    ai_service = FakeSchemaAiService(signals_json)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    return TrackingService(db, ai_service, project_service, metrics)


def _session_id(db) -> int:
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    return db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )


async def test_a_matching_trigger_fires_when_auto_tracking_is_enabled(db):
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db)
    service = _tracking_service(db, automaton, '{"mySignal": 1}')

    result = await service.process(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_matching_trigger_does_not_fire_when_auto_tracking_is_frozen(db):
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db)
    service = _tracking_service(db, automaton, '{"mySignal": 1}')
    service.auto_tracking_enabled = False

    result = await service.process(session_id, "hello")

    assert result["state_changed"] is False
    assert result["new_state"] is None


async def test_signals_are_still_computed_and_logged_while_frozen(db):
    """The whole point: freezing the *transition* must never also freeze
    signal computation — the Signals tab still needs something to show."""
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db)
    service = _tracking_service(db, automaton, '{"mySignal": 1}')
    service.auto_tracking_enabled = False

    await service.process(session_id, "hello")

    logged = service.get_session_signals(session_id)
    assert len(logged) == 1
    assert json.loads(logged[0]["values"])["mySignal"] == 1
