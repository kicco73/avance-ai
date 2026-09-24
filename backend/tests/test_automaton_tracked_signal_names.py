"""Automaton.tracked_signal_names — which of a project's declared
signals a turn in a given state computes: with the default
`signal-tracking-strategy: relevant`, the ones referenced (as `signal.<name>`) by
at least one action leaving that state; with `all`, every declared one.
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


def _automaton(
    signals: list[Signal], actions_a: list[Action], actions_b: list[Action] | None = None,
    strategy_a: str = "relevant",
) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
        "a": State(
            input_processor="ai", key="a", ui_label="A", final=not actions_a, contextual_prompt="hi", actions=actions_a,
            signal_tracking_strategy=strategy_a,
        ),
    }
    if actions_b is not None:
        states["b"] = State(input_processor="ai", key="b", ui_label="B", final=not actions_b, contextual_prompt="bye", actions=actions_b)
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=signals,
        general_attachments={}, autotracking_on_ai_message=False,
    )


@pytest.mark.parametrize(("actions", "signals", "expected"), [
    ([_action("advance", trigger="signal.mood >= 50")], [MOOD], {"mood"}),
    ([_action("advance", trigger="signal.mood >= 50")], [MOOD, UNUSED], {"mood"}),
    ([_action("advance", on_exit="env.last_mood = signal.mood")], [MOOD], {"mood"}),
    (
        [_action("advance", trigger="signal.mood >= 50", on_exit="env.last_stability = signal.stability")],
        [MOOD, STABILITY], {"mood", "stability"},
    ),
    (
        [_action("a1", trigger="signal.mood >= 50"), _action("a2", trigger="retention >= 1 and signal.stability >= 1")],
        [MOOD, STABILITY], {"mood", "stability"},
    ),
    ([_action("a1", on_exit="env.reset = True"), _action("a2", trigger="signal.mood >= 50")], [MOOD], {"mood"}),
    ([_action("advance", trigger="engagement >= 1")], [MOOD], set()),
    ([_action("advance")], [MOOD], set()),
    ([], [MOOD], set()),
    ([_action("advance", on_exit="env.reset_counter = True")], [MOOD], set()),
    (
        [_action("advance", on_exit="env.number_of_steps = env.number_of_steps + 1\nenv.last_engagement = engagement")],
        [MOOD], set(),
    ),
    (
        [_action("advance", trigger="1", on_exit="if signal.mood > 1:\n    env.x = 1\nelse:\n    chat.say(signal.stability)")],
        [MOOD, STABILITY], {"mood", "stability"},
    ),
    ([_action("advance", trigger="1", task="task.send_mail(user.email, signal.mood)")], [MOOD], {"mood"}),
    (
        [_action("advance", trigger="1", task="task.defer(lambda: task.send_mail(user.email, signal.mood), when)")],
        [MOOD], {"mood"},
    ),
], ids=[
    "trigger", "excludes-unreferenced-signal", "on-exit-only", "trigger-and-on-exit-same-action",
    "several-actions", "on-exit-on-one-trigger-on-another", "metric-name", "no-trigger",
    "final-state", "literal-on-exit", "metric-or-env-key-in-on-exit",
    "on-exit-if-and-call", "task", "task-defer",
])
def test_only_signals_a_states_own_scripts_reference_are_reported(actions, signals, expected):
    assert _automaton(signals, actions).tracked_signal_names("a") == expected


def test_all_tracked_signal_names_unions_every_state_excluding_what_nothing_references():
    referencing = _automaton(
        [MOOD, STABILITY, UNUSED],
        [_action("a1", trigger="signal.mood >= 50")],
        [_action("b1", target="b", trigger="signal.stability >= 1")],
    )
    assert referencing.all_tracked_signal_names() == {"mood", "stability"}

    assert _automaton([MOOD], [_action("a1")], []).all_tracked_signal_names() == set()


@pytest.mark.parametrize("actions", [
    [_action("advance", trigger="signal.mood >= 50")],
    [_action("advance")],
    [],
], ids=["one-referenced", "no-trigger", "final-state"])
def test_strategy_all_tracks_every_declared_signal_whatever_the_actions_reference(actions):
    assert _automaton([MOOD, STABILITY, UNUSED], actions, strategy_a="all").tracked_signal_names("a") == {
        "mood", "stability", "unused",
    }


def test_strategy_all_in_one_state_does_not_widen_another_states_relevant_set():
    automaton = _automaton(
        [MOOD, STABILITY, UNUSED], [], [_action("b1", target="b", trigger="signal.stability >= 1")], strategy_a="all",
    )
    assert automaton.tracked_signal_names("b") == {"stability"}
    assert automaton.all_tracked_signal_names() == {"mood", "stability", "unused"}
