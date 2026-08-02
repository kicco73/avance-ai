from __future__ import annotations

import json
from datetime import datetime

from automaton.automaton import Action, Automaton, Signal, State
from chat.auto_tracker import AutoTracker
from chat.env import Env
from chat.metadata_handler import MetadataHandler
from chat.metrics_service import ChatMetrics
from chat.signal_evaluator import SignalEvaluator
from chat.signals import Signals

USERNAME = "user"
PROJECT_NAME = "proj"


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
        # AutoTracker.run) now coerces signal_values against exactly
        # this list, dropping anything not declared here, same as the
        # old explicit-computation path already did.
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=True,
        autotracking_on_ai_message=False,
    )


def _tracker(db, automaton: Automaton) -> AutoTracker:
    metrics = ChatMetrics(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    signal_evaluator = SignalEvaluator(MetadataHandler())
    return AutoTracker(db, ai_service=None, signals=signals, metrics=metrics, env=env, signal_evaluator=signal_evaluator)


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

    action, new_state, _ = await _tracker(db, automaton).run(
        pending_message=None,
        project_name=PROJECT_NAME,
        session_id=session_id,
        automaton=automaton,
        state=automaton.states["a"],
        signal_values={"mySignal": 1},
    )

    assert action is not None and action.name == "advance"
    assert new_state.key == "b"


async def test_a_metric_referencing_trigger_that_is_not_met_does_not_fire(db):
    automaton = _automaton_with_trigger("engagement >= 99")
    session_id = _session_id(db)

    action, new_state, _ = await _tracker(db, automaton).run(
        pending_message=None,
        project_name=PROJECT_NAME,
        session_id=session_id,
        automaton=automaton,
        state=automaton.states["a"],
        signal_values={"mySignal": 1},
    )

    assert action is None
    assert new_state.key == "a"


async def test_metric_values_used_for_evaluation_are_never_persisted(db):
    automaton = _automaton_with_trigger("engagement >= 1")
    session_id = _session_id(db)

    await _tracker(db, automaton).run(
        pending_message=None,
        project_name=PROJECT_NAME,
        session_id=session_id,
        automaton=automaton,
        state=automaton.states["a"],
        signal_values={"mySignal": 42},
    )

    persisted = db.get_signals(session_id)
    assert len(persisted) == 1
    # Only the real signal is stored — "engagement" (or any other metric)
    # must never leak into the Signals log, or SignalStabilityMetric would
    # start treating metric values as if they were domain signals.
    assert json.loads(persisted[0]["values"]) == {"mySignal": 42}


async def test_metrics_are_never_computed_when_no_trigger_in_the_state_references_one(db, monkeypatch):
    automaton = _automaton_with_trigger("mySignal >= 1")
    session_id = _session_id(db)
    tracker = _tracker(db, automaton)
    calls = []
    monkeypatch.setattr(tracker._metrics, "calculate_values", lambda: calls.append(1) or {})

    await tracker.run(
        pending_message=None,
        project_name=PROJECT_NAME,
        session_id=session_id,
        automaton=automaton,
        state=automaton.states["a"],
        signal_values={"mySignal": 42},
    )

    assert calls == []


async def test_a_trigger_can_combine_a_signal_and_a_metric(db):
    automaton = _automaton_with_trigger("mySignal >= 40 and engagement >= 1")
    session_id = _session_id(db)

    action, new_state, _ = await _tracker(db, automaton).run(
        pending_message=None,
        project_name=PROJECT_NAME,
        session_id=session_id,
        automaton=automaton,
        state=automaton.states["a"],
        signal_values={"mySignal": 42},
    )

    assert action is not None
    assert new_state.key == "b"
