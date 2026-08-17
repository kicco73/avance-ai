"""Auto-tracking's own metric-in-trigger support: a trigger expression
can reference a metrics.metrics_framework core metric (e.g. `engagement`)
alongside/instead of a declared signal — merged in only when actually
referenced (see metrics.metric_service.MetricService.merge_if_referenced),
and never itself persisted onto the Tracking row (only real signals are).

Rewritten for this refactor: `tracking/auto_tracker.py`'s `AutoTracker`
class no longer exists at all (deleted — ground truth table row #5).
Replaced by TrackingProcessorAfterUserMessage/TrackingProcessorAfterAiMessage
(see tracking/tracking_processor.py's _would_trigger_action/_move_automaton),
constructed by TrackingService.process(). These tests now drive the same
behavior through TrackingService.process() directly, in
autotracking_on_ai_message mode (autotracking_on_user_message=False,
which is what tracking_service.py:193-196 actually consults for
processor selection) — a single, deterministic AI call per turn, the
closest current equivalent to AutoTracker.run's own single-shot
semantics.
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

# Every test here verifies a specific, punctual fact about how a metric
# participates in trigger evaluation (fires/doesn't fire, never leaks
# into the persisted Tracking row, metric computation is skipped when
# unreferenced) — still real current behavior, just driven through a
# different entry point now.
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
        # A real declared signal — SignalEvaluator.validate (see
        # TrackingProcessor._would_trigger_action) now coerces
        # signal_values against exactly this list, dropping anything not
        # declared here, same as the old explicit-computation path
        # already did.
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=False,
        autotracking_on_ai_message=True,
    )


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME


class FakeSchemaAiService:
    """A v2 (schema)-shaped fake — reports `signals` straight through
    on_metadata as a raw JSON string, the same wire shape
    ai.ai_service.AiService.generate_stream_with_metadata actually uses
    (see tracking/turn_protocol_using_schema.py)."""

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
    # TrackingService.__init__ now takes project_service directly, not
    # get_active_automaton/get_username/get_active_project_name callables
    # (see tracking/tracking_service.py).
    return TrackingService(db, ai_service, project_service, metrics)


def _session_id(db) -> int:
    # A freshly bootstrapped session, with no messages, already scores
    # "engagement" above zero via its own session component alone (see
    # tests/test_controller_metrics.py) — enough to drive these triggers
    # without needing to fabricate a message history.
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
    # mySignal must appear in the trigger too, not just engagement — see
    # Automaton.triggerable_signal_names/TrackingProcessor._would_trigger_
    # action's own signal-scoping optimization: a signal no trigger
    # leaving this state references at all is dropped before persisting,
    # same as a metric always was, so a trigger that only ever names a
    # metric would (correctly) filter mySignal out too and defeat this
    # test's own actual point below.
    automaton = _automaton_with_trigger("mySignal >= 1 and engagement >= 1")
    session_id = _session_id(db)

    await _tracking_service(db, automaton, '{"mySignal": 42}').process(session_id, "hello")

    persisted = db.get_signals(session_id)
    assert len(persisted) == 1
    # Only the real, model-reported signal is stored — _move_automaton
    # (tracking/tracking_processor.py) persists self.metadata.signals
    # verbatim, never the metrics/env values merged in only for trigger
    # evaluation — "engagement" (or any other metric) must never leak
    # into the Tracking log, or SignalStabilityMetric would start
    # treating metric values as if they were domain signals.
    assert json.loads(persisted[0]["values"]) == {"mySignal": 42}


async def test_metrics_are_never_computed_when_no_trigger_in_the_state_references_one(db, monkeypatch):
    automaton = _automaton_with_trigger("mySignal >= 1")
    session_id = _session_id(db)
    tracking_service = _tracking_service(db, automaton, '{"mySignal": 42}')
    calls = []
    monkeypatch.setattr(tracking_service._metrics, "calculate_values", lambda: calls.append(1) or {})

    await tracking_service.process(session_id, "hello")

    assert calls == []


async def test_a_trigger_can_combine_a_signal_and_a_metric(db):
    automaton = _automaton_with_trigger("mySignal >= 40 and engagement >= 1")
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 42}').process(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"
