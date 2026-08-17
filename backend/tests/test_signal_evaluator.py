"""Tests for tracking.evaluator.SignalEvaluator.validate — coercing raw
signal values (however they were actually obtained, see chat.
turn_strategy.TurnStrategy.compute_explicitly/test_turn_strategy_compute_explicitly.py
for the part that gets them) against an automaton's own declared
signals.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from tracking.evaluator import SignalEvaluator

pytestmark = pytest.mark.contract


def _automaton(signals=None) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    state_a = State(key="a", ui_label="A", final=True, contextual_prompt="hi")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=signals or [],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def _evaluator() -> SignalEvaluator:
    return SignalEvaluator()


def test_validate_coerces_a_valid_numeric_value():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": 42})
    assert result == {"mood": 42}


def test_validate_turns_a_non_numeric_value_into_none():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": "high"})
    assert result == {"mood": None}


def test_validate_turns_a_boolean_into_none():
    """bool is a subclass of int in Python — explicitly excluded."""
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": True})
    assert result == {"mood": None}


def test_validate_fills_in_none_for_a_missing_declared_signal():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {})
    assert result == {"mood": None}


def test_validate_drops_anything_not_a_declared_signal():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": 1, "somethingElse": 99})
    assert result == {"mood": 1}


def test_validate_of_none_raw_values_fills_every_declared_signal_with_none():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d"), Signal(name="b", ui_label="B", definition="d")])
    assert _evaluator().validate(automaton, None) == {"a": None, "b": None}


def test_validate_with_names_restricts_the_result_to_that_subset():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d"), Signal(name="b", ui_label="B", definition="d")])
    result = _evaluator().validate(automaton, {"a": 1, "b": 2}, names={"a"})
    assert result == {"a": 1}


def test_validate_with_names_still_fills_none_for_a_missing_needed_signal():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d"), Signal(name="b", ui_label="B", definition="d")])
    result = _evaluator().validate(automaton, {}, names={"a"})
    assert result == {"a": None}


def test_validate_with_an_empty_names_set_returns_nothing():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d")])
    result = _evaluator().validate(automaton, {"a": 1}, names=set())
    assert result == {}
