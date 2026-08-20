"""Signals.get_definition — the "Definition of signals:" block embedded
in every turn/signals-computation prompt, optionally scoped to a subset
of declared signals.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from tracking.definitions import Signals

pytestmark = pytest.mark.contract


def _automaton(signals: list[Signal]) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]),
                "a": State(key="a", ui_label="A", final=True, contextual_prompt="hi")},
        general_prompt="",
        signals=signals,
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def _signals(automaton: Automaton) -> Signals:
    return Signals(get_active_automaton=lambda: automaton, db=None)


def test_no_names_includes_every_declared_signal():
    automaton = _automaton([
        Signal(name="mood", ui_label="Mood", definition="mood definition"),
        Signal(name="engagement_level", ui_label="Engagement", definition="engagement definition"),
    ])

    result = _signals(automaton).get_definition()

    assert "mood definition" in result
    assert "engagement definition" in result


def test_names_restricts_to_that_subset():
    automaton = _automaton([
        Signal(name="mood", ui_label="Mood", definition="mood definition"),
        Signal(name="engagement_level", ui_label="Engagement", definition="engagement definition"),
    ])

    result = _signals(automaton).get_definition(names={"mood"})

    assert "mood definition" in result
    assert "engagement definition" not in result


def test_empty_names_includes_no_signal_definitions():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="mood definition")])

    result = _signals(automaton).get_definition(names=set())

    assert "mood definition" not in result
