"""Auto-tracking's metric-in-trigger support: a trigger expression can
reference a core metric (e.g. `engagement`) alongside/instead of a
declared signal, merged in only when referenced, never persisted onto
the Tracking row.
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

# Each test verifies one fact about metric-in-trigger evaluation:
# fires/doesn't fire, never leaks into the persisted Tracking row,
# computation is skipped when unreferenced.
pytestmark = pytest.mark.regression


def _automaton_with_trigger(trigger_expr: str, target: str = "b") -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target=target, trigger=trigger_expr)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye", actions=[])
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
        # A real declared signal — signal_values are coerced against
        # exactly this list, dropping anything not declared here.
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

    def get_project_availability(self, project_name: str):
        return (False, None)


class FakeSchemaAiService:
    """A v2 (schema)-shaped fake — reports `signals` straight through
    on_metadata as a raw JSON string."""

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


def _tracking_service(db, automaton: Automaton, signals_json: str) -> TrackingService:
    ai_service = FakeSchemaAiService(signals_json)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    return TrackingService(db, ai_service, project_service, metrics)


def _session_id(db) -> int:
    # A freshly bootstrapped session already scores "engagement" above
    # zero via its session component alone, enough to drive these triggers.
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    return db.create_chat_session(
        username=USERNAME,
        project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1),
        datetime_end=datetime(2026, 1, 1),
        start_state="a",
        end_state="a",
    )


async def test_a_trigger_referencing_only_a_metric_can_fire(db):
    automaton = _automaton_with_trigger("engagement >= 1")
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_metric_referencing_trigger_that_is_not_met_does_not_fire(db):
    automaton = _automaton_with_trigger("engagement >= 99")
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    assert result["state_changed"] is False
    assert result["new_state"] is None


async def test_metric_values_used_for_evaluation_are_never_persisted(db):
    # mySignal must appear in the trigger too, not just engagement — a
    # signal no trigger references is dropped before persisting, same as
    # a metric, so an engagement-only trigger would filter it out too.
    automaton = _automaton_with_trigger("signal.mySignal >= 1 and engagement >= 1")
    session_id = _session_id(db)

    await _tracking_service(db, automaton, '{"mySignal": 42}').process(session_id, "hello")

    persisted = db.get_signals(session_id)
    assert len(persisted) == 1
    # Only the real, model-reported signal is stored — "engagement" (or
    # any metric) must never leak into the Tracking log.
    assert json.loads(persisted[0]["values"]) == {"mySignal": 42}


async def test_metrics_are_never_computed_when_no_trigger_in_the_state_references_one(db, monkeypatch):
    automaton = _automaton_with_trigger("signal.mySignal >= 1")
    session_id = _session_id(db)
    tracking_service = _tracking_service(db, automaton, '{"mySignal": 42}')
    calls = []
    monkeypatch.setattr(tracking_service._metrics, "calculate_values", lambda: calls.append(1) or {})

    await tracking_service.process(session_id, "hello")

    assert calls == []


async def test_a_trigger_can_combine_a_signal_and_a_metric(db):
    automaton = _automaton_with_trigger("signal.mySignal >= 40 and engagement >= 1")
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 42}').process(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"
