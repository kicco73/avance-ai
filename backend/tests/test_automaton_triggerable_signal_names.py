"""Automaton.triggerable_signal_names — the subset of a project's declared
signals actually referenced (as `signal.<name>`) by at least one
triggerable action leaving a given state.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State

pytestmark = pytest.mark.contract

MOOD = Signal(name="mood", ui_label="Mood", definition="d")
STABILITY = Signal(name="stability", ui_label="Stability", definition="d")
UNUSED = Signal(name="unused", ui_label="Unused", definition="d")


def _action(name: str, target: str = "a", **fields) -> Action:
    return Action(name=name, ui_label=name.upper(), ui_button=name.upper(), target=target, **fields)


def _automaton(signals: list[Signal], actions_a: list[Action], actions_b: list[Action] | None = None) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="A", final=not actions_a, contextual_prompt="hi", actions=actions_a),
    }
    if actions_b is not None:
        states["b"] = State(key="b", ui_label="B", final=not actions_b, contextual_prompt="bye", actions=actions_b)
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=signals,
        attachments={}, general_attachments={}, autotracking_on_ai_message=False,
    )


@pytest.mark.parametrize(("actions", "signals", "expected"), [
    ([_action("advance", trigger="signal.mood >= 50")], [MOOD], {"mood"}),
    ([_action("advance", trigger="signal.mood >= 50")], [MOOD, UNUSED], {"mood"}),
    ([_action("advance", env={"last_mood": "signal.mood"})], [MOOD], {"mood"}),
    (
        [_action("advance", trigger="signal.mood >= 50", env={"last_stability": "signal.stability"})],
        [MOOD, STABILITY], {"mood", "stability"},
    ),
    (
        [_action("a1", trigger="signal.mood >= 50"), _action("a2", trigger="retention >= 1 and signal.stability >= 1")],
        [MOOD, STABILITY], {"mood", "stability"},
    ),
    ([_action("a1", env={"reset": "True"}), _action("a2", trigger="signal.mood >= 50")], [MOOD], {"mood"}),
    ([_action("advance", trigger="engagement >= 1")], [MOOD], set()),
    ([_action("advance")], [MOOD], set()),
    ([], [MOOD], set()),
    ([_action("advance", env={"reset_counter": "True"})], [MOOD], set()),
    (
        [_action("advance", env={"number_of_steps": "env.number_of_steps + 1", "last_engagement": "engagement"})],
        [MOOD], set(),
    ),
], ids=[
    "trigger", "excludes-unreferenced-signal", "env-only", "trigger-and-env-same-action",
    "several-actions", "env-on-one-trigger-on-another", "metric-name", "no-trigger",
    "final-state", "literal-env", "metric-or-env-key-in-env",
])
def test_only_signals_a_states_own_triggers_or_env_expressions_reference_are_reported(actions, signals, expected):
    assert _automaton(signals, actions).triggerable_signal_names("a") == expected


def test_all_triggerable_signal_names_unions_every_state_excluding_what_nothing_references():
    referencing = _automaton(
        [MOOD, STABILITY, UNUSED],
        [_action("a1", trigger="signal.mood >= 50")],
        [_action("b1", target="b", trigger="signal.stability >= 1")],
    )
    assert referencing.all_triggerable_signal_names() == {"mood", "stability"}

    assert _automaton([MOOD], [_action("a1")], []).all_triggerable_signal_names() == set()
