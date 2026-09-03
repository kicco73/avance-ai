"""TrackingService.set_auto_tracking_enabled/is_auto_tracking_enabled —
the "Dev mode: freeze automatic state transitions" toggle, per 'test'
session; a native session can never be frozen. Signal evaluation is
never gated by this — only whether a triggered action gets applied.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metric_service import MetricService
from tracking.fixed_project_context import FixedProjectContext
from conftest import make_test_actuator_factory
from tracking.tracking_service import TrackingService

USERNAME = "user"
PROJECT_ID = "proj"

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

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_project_availability(self, project_id: str):
        return (False, None)


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


def _tracking_service(db, automaton: Automaton, signals_json: str = '{"mySignal": 1}') -> tuple[TrackingService, FakeSchemaAiService]:
    ai_service = FakeSchemaAiService(signals_json)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, FixedProjectContext(project_id=PROJECT_ID))
    return TrackingService(db, project_service, metrics, make_test_actuator_factory(db)), ai_service


def _session_id(db, *, type: str = "test") -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a", type=type,
    )


async def test_a_matching_trigger_fires_when_auto_tracking_is_enabled(db):
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_matching_trigger_does_not_fire_when_auto_tracking_is_frozen(db):
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')
    service.set_auto_tracking_enabled(session_id, False)

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is False
    assert result["new_state"] is None


async def test_signals_are_still_computed_and_logged_while_frozen(db):
    """The whole point: freezing the *transition* must never also freeze
    signal computation — the Signals tab still needs something to show."""
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')
    service.set_auto_tracking_enabled(session_id, False)

    await service._process(session_id, "hello", ai_service)

    logged = service.get_session_signals(session_id)
    assert len(logged) == 1
    assert json.loads(logged[0]["values"])["mySignal"] == 1


async def test_freezing_a_live_session_has_no_effect(db):
    """Auto-tracking freeze only ever applies to 'test' sessions — a
    live session's trigger still fires normally even if
    set_auto_tracking_enabled(session_id, False) was called for it."""
    automaton = _automaton("signal.mySignal >= 1")
    session_id = _session_id(db, type="live")
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')
    service.set_auto_tracking_enabled(session_id, False)

    result = await service._process(session_id, "hello", ai_service)

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_freezing_one_test_session_never_affects_another(db):
    """Not global: freezing session A must never freeze session B, even
    though both are 'test' sessions of the same project. Both are
    created against the same publish, to avoid a second publish deleting
    the first session's draft test session."""
    automaton = _automaton("signal.mySignal >= 1")
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_kwargs = dict(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a", type="test",
    )
    frozen_session_id = db.create_chat_session(**session_kwargs)
    other_session_id = db.create_chat_session(**session_kwargs)
    service, ai_service = _tracking_service(db, automaton, '{"mySignal": 1}')
    service.set_auto_tracking_enabled(frozen_session_id, False)

    result = await service._process(other_session_id, "hello", ai_service)

    assert result["state_changed"] is True
    assert result["new_state"] == "b"
