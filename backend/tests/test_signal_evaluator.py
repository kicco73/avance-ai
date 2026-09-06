"""Tests for tracking.evaluator.SignalEvaluator.validate — coercing raw
signal values against an automaton's own declared signals.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from tracking.evaluator import SignalEvaluator

pytestmark = pytest.mark.contract


def _automaton(*names: str) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    state_a = State(key="a", ui_label="A", final=True, contextual_prompt="hi")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[Signal(name=name, ui_label=name.upper(), definition="d") for name in names],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def _validate(automaton, raw, **kwargs):
    return SignalEvaluator().validate(automaton, raw, **kwargs)


@pytest.mark.parametrize(("raw", "expected"), [
    ({"mood": 42}, {"mood": 42}),
    ({"mood": "high"}, {"mood": None}),
    ({"mood": True}, {"mood": None}),
    ({}, {"mood": None}),
    (None, {"mood": None}),
    ({"mood": 1, "somethingElse": 99}, {"mood": 1}),
], ids=["numeric", "non-numeric", "bool", "missing", "none", "undeclared-dropped"])
def test_validate_keeps_only_declared_signals_coercing_anything_but_a_real_number_to_none(raw, expected):
    """bool is a subclass of int in Python — explicitly excluded."""
    assert _validate(_automaton("mood"), raw) == expected


@pytest.mark.parametrize(("raw", "names", "expected"), [
    ({"a": 1, "b": 2}, {"a"}, {"a": 1}),
    ({}, {"a"}, {"a": None}),
    ({"a": 1}, set(), {}),
], ids=["subset", "missing-still-filled", "empty-set"])
def test_names_restricts_the_result_to_that_subset_still_filling_none_for_a_missing_one(raw, names, expected):
    assert _validate(_automaton("a", "b"), raw, names=names) == expected
