"""Automaton.triggerable_signal_names — the subset of a project's declared
signals actually referenced (as `signal.<name>`) by at least one
triggerable action leaving a given state.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State

pytestmark = pytest.mark.contract


def _automaton(actions: list[Action], signals: list[Signal]) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="A", final=not actions, contextual_prompt="hi", actions=actions),
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=signals,
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def test_returns_a_signal_referenced_by_the_states_own_trigger():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger="signal.mood >= 50")
    automaton = _automaton([action], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == {"mood"}


def test_excludes_a_declared_signal_no_trigger_here_references():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger="signal.mood >= 50")
    automaton = _automaton(
        [action],
        [Signal(name="mood", ui_label="Mood", definition="d"), Signal(name="unused", ui_label="Unused", definition="d")],
    )

    assert automaton.triggerable_signal_names("a") == {"mood"}


def test_excludes_a_metric_name_even_though_it_is_referenced():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger="engagement >= 1")
    automaton = _automaton([action], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == set()


def test_combines_signals_from_multiple_actions():
    action1 = Action(name="a1", ui_label="A1", ui_button="A1", target="a", trigger="signal.mood >= 50")
    action2 = Action(name="a2", ui_label="A2", ui_button="A2", target="a", trigger="retention >= 1 and signal.stability >= 1")
    automaton = _automaton(
        [action1, action2],
        [Signal(name="mood", ui_label="Mood", definition="d"), Signal(name="stability", ui_label="Stability", definition="d")],
    )

    assert automaton.triggerable_signal_names("a") == {"mood", "stability"}


def test_empty_for_a_manual_only_action_with_no_trigger():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    automaton = _automaton([action], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == set()


def test_empty_for_a_final_state_with_no_actions_at_all():
    automaton = _automaton([], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == set()


def test_includes_a_signal_referenced_only_in_the_env_field():
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="a",
        env={"last_mood": "signal.mood"},
    )
    automaton = _automaton([action], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == {"mood"}


def test_combines_signals_from_trigger_and_env_on_the_same_action():
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="a",
        trigger="signal.mood >= 50", env={"last_stability": "signal.stability"},
    )
    automaton = _automaton(
        [action],
        [Signal(name="mood", ui_label="Mood", definition="d"), Signal(name="stability", ui_label="Stability", definition="d")],
    )

    assert automaton.triggerable_signal_names("a") == {"mood", "stability"}


def test_env_field_signal_on_one_action_and_trigger_signal_on_another():
    action1 = Action(name="a1", ui_label="A1", ui_button="A1", target="a", env={"reset": "True"})
    action2 = Action(name="a2", ui_label="A2", ui_button="A2", target="a", trigger="signal.mood >= 50")
    automaton = _automaton([action1, action2], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == {"mood"}


def test_excludes_a_metric_or_free_form_env_key_referenced_only_in_env():
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="a",
        env={"number_of_steps": "env.number_of_steps + 1", "last_engagement": "engagement"},
    )
    automaton = _automaton([action], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == set()


def test_a_literal_env_expression_with_no_references_contributes_nothing():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env={"reset_counter": "True"})
    automaton = _automaton([action], [Signal(name="mood", ui_label="Mood", definition="d")])

    assert automaton.triggerable_signal_names("a") == set()


def _multi_state_automaton(signals: list[Signal], actions_a: list[Action], actions_b: list[Action]) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="A", final=not actions_a, contextual_prompt="hi", actions=actions_a),
        "b": State(key="b", ui_label="B", final=not actions_b, contextual_prompt="bye", actions=actions_b),
    }
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=signals,
        attachments={}, general_attachments={},
        autotracking_on_ai_message=False,
    )


def test_all_triggerable_signal_names_unions_across_every_state():
    action_a = Action(name="a1", ui_label="A1", ui_button="A1", target="a", trigger="signal.mood >= 50")
    action_b = Action(name="b1", ui_label="B1", ui_button="B1", target="b", trigger="signal.stability >= 1")
    automaton = _multi_state_automaton(
        [Signal(name="mood", ui_label="Mood", definition="d"), Signal(name="stability", ui_label="Stability", definition="d")],
        [action_a], [action_b],
    )

    assert automaton.all_triggerable_signal_names() == {"mood", "stability"}


def test_all_triggerable_signal_names_excludes_a_signal_no_state_references():
    action_a = Action(name="a1", ui_label="A1", ui_button="A1", target="a", trigger="signal.mood >= 50")
    automaton = _multi_state_automaton(
        [Signal(name="mood", ui_label="Mood", definition="d"), Signal(name="unused", ui_label="Unused", definition="d")],
        [action_a], [],
    )

    assert automaton.all_triggerable_signal_names() == {"mood"}


def test_all_triggerable_signal_names_empty_when_nothing_anywhere_references_a_signal():
    action_a = Action(name="a1", ui_label="A1", ui_button="A1", target="a")
    automaton = _multi_state_automaton([Signal(name="mood", ui_label="Mood", definition="d")], [action_a], [])

    assert automaton.all_triggerable_signal_names() == set()
