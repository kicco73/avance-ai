"""AutoTracker's own end of the action-level `env` feature: once a
trigger fires an action, that action's `env` field (see
automaton_builder.py's _build_action/Automaton.eval_action_env) is
evaluated and merged onto chat.env.Env's persisted store — so the very
next prompt (see chat_service.py's _build_turn_prompt ->
MetadataHandler.build_prompt) already sees the updated value, not last
turn's.
"""
from __future__ import annotations

from datetime import datetime

from automaton.automaton import Action, Automaton, Signal, State
from tracking.auto_tracker import AutoTracker
from chat.env import Env
from metrics.metric_service import MetricService
from tracking.evaluator import SignalEvaluator
from tracking.definitions import Signals

USERNAME = "user"
PROJECT_NAME = "proj"


def _automaton_with_env(trigger_expr: str, action_env: dict, target: str = "b") -> Automaton:
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
        autotracking_on_user_message=True,
        autotracking_on_ai_message=False,
    )


def _tracker(db, automaton: Automaton) -> AutoTracker:
    metrics = MetricService(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    signal_evaluator = SignalEvaluator()
    return AutoTracker(db, ai_service=None, signals=signals, metrics=metrics, env=env, signal_evaluator=signal_evaluator)


def _session_id(db) -> int:
    return db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )


async def test_a_fired_actions_env_is_persisted(db):
    automaton = _automaton_with_env("mySignal >= 1", {"reset_counter": "True"})
    session_id = _session_id(db)

    action, _, _ = await _tracker(db, automaton).run(
        pending_message=None, project_name=PROJECT_NAME, session_id=session_id,
        automaton=automaton, state=automaton.states["a"], signal_values={"mySignal": 1},
    )

    assert action is not None
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    # Lands in the action-set store (see Env.update_action_set) — the
    # Inspector Env tab's own "SET" section — never the model-reported
    # `stored()` one (its own "AI" section).
    assert env.action_set() == {"reset_counter": True}
    assert env.stored() == {}


async def test_env_is_not_touched_when_the_trigger_does_not_fire(db):
    automaton = _automaton_with_env("mySignal >= 99", {"reset_counter": "True"})
    session_id = _session_id(db)

    action, _, _ = await _tracker(db, automaton).run(
        pending_message=None, project_name=PROJECT_NAME, session_id=session_id,
        automaton=automaton, state=automaton.states["a"], signal_values={"mySignal": 1},
    )

    assert action is None
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    assert env.get("reset_counter") is None


async def test_an_env_expression_can_self_reference_the_previous_stored_value(db):
    automaton = _automaton_with_env("mySignal >= 1", {"number_of_steps": "number_of_steps + 1"}, target="a")
    session_id = _session_id(db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    # Seeded directly in the action-set store (see Env.update_action_set)
    # — as a real int, since that's what simpleeval always produces,
    # unlike a value the model itself reported via [env], which is
    # always a plain string (see MetadataHandler._parse_env_tag).
    env.update_action_set({"number_of_steps": 3})

    action, _, _ = await _tracker(db, automaton).run(
        pending_message=None, project_name=PROJECT_NAME, session_id=session_id,
        automaton=automaton, state=automaton.states["a"], signal_values={"mySignal": 1},
    )

    assert action is not None  # a self-loop (target == "a") still counts as fired
    assert env.action_set()["number_of_steps"] == 4


async def test_self_referencing_an_env_key_that_was_never_stored_yet_leaves_it_unset(db):
    automaton = _automaton_with_env("mySignal >= 1", {"number_of_steps": "number_of_steps + 1"}, target="a")
    session_id = _session_id(db)

    await _tracker(db, automaton).run(
        pending_message=None, project_name=PROJECT_NAME, session_id=session_id,
        automaton=automaton, state=automaton.states["a"], signal_values={"mySignal": 1},
    )

    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    assert env.get("number_of_steps") is None


async def test_env_can_reference_a_signal_value_from_this_same_turn(db):
    automaton = _automaton_with_env("mySignal >= 1", {"last_signal": "mySignal"})
    session_id = _session_id(db)

    await _tracker(db, automaton).run(
        pending_message=None, project_name=PROJECT_NAME, session_id=session_id,
        automaton=automaton, state=automaton.states["a"], signal_values={"mySignal": 7},
    )

    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    assert env.get("last_signal") == 7


async def test_an_action_with_no_env_field_never_touches_env_at_all(db, monkeypatch):
    automaton = _automaton_with_env("mySignal >= 1", None)
    session_id = _session_id(db)
    tracker = _tracker(db, automaton)
    calls = []
    monkeypatch.setattr(tracker._env, "update", lambda *a, **k: calls.append(1))

    await tracker.run(
        pending_message=None, project_name=PROJECT_NAME, session_id=session_id,
        automaton=automaton, state=automaton.states["a"], signal_values={"mySignal": 1},
    )

    assert calls == []
