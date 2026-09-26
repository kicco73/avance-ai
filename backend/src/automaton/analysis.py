"""Static analysis of an automaton's own expression text — the questions
whose answers are fixed the moment a project's index.yml is written, and
which the Automaton used to re-derive by re-parsing that text on every
call, in the middle of a request.

Three of them, all pure functions of the declared actions:

  - which env keys a project declares or assigns anywhere
  - which of its signals a given state's trigger, on-exit and task scripts reference
  - which bare names a given state's triggers mention (metric names, in
    practice)

They live here, apart from automaton.py, for one structural reason: this
is the only place that needs TriggerExpressionAnalyzer for something
other than actually evaluating an expression. An automaton that does not
carry its expression text at all — a compiled one, whose triggers and
scripts are literal Python — has nothing to analyse, and supplies these
answers as data instead of calling anything here.

Each function keeps the exact error behaviour its caller had before it
moved here: declared_env_key_names swallows a malformed on-exit (build
validation already rules those out; a hand-built automaton in a test may
not), the other two let it raise.
"""
from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .on_exit_expression_analyzer import OnExitExpressionAnalyzer
from .trigger_expression_analyzer import TriggerExpressionAnalyzer

if TYPE_CHECKING:
    from .model import Action, EnvKey, State


def on_exit_assigned_keys(on_exit: str | None) -> set[str]:
    """The env key names an `on-exit` script writes — statically, by
    parsing its `env.<key> = expr` lines, never by evaluating them. A
    malformed script or a non-assignment line contributes nothing rather
    than raising."""
    if not on_exit:
        return set()
    try:
        statements = TriggerExpressionAnalyzer.task_statements(on_exit)
    except SyntaxError:
        return set()
    keys: set[str] = set()
    for _line_number, statement in OnExitExpressionAnalyzer.flattened_statements(statements):
        assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
        if assignment is not None:
            keys.add(assignment[0])
    return keys


def declared_env_key_names(
    env_keys: list["EnvKey"], init_action: "Action", states: dict[str, "State"],
) -> set[str]:
    names = {env_key.name for env_key in env_keys}
    names |= on_exit_assigned_keys(init_action.on_exit)
    for state in states.values():
        for action in state.actions:
            names |= on_exit_assigned_keys(action.on_exit)
    return names


class RelevantSignalsTracking:
    def tracked_signal_names(self, state: "State", declared_signal_names: set[str]) -> set[str]:
        return referenced_signal_names(state, declared_signal_names)

    def read_signal_names(self, declared_signal_names: set[str]) -> set[str]:
        return set()


class AllSignalsTracking:
    def tracked_signal_names(self, state: "State", declared_signal_names: set[str]) -> set[str]:
        return set(declared_signal_names)

    def read_signal_names(self, declared_signal_names: set[str]) -> set[str]:
        return set()


class ReadOnlySignalsTracking:
    def tracked_signal_names(self, state: "State", declared_signal_names: set[str]) -> set[str]:
        return set()

    def read_signal_names(self, declared_signal_names: set[str]) -> set[str]:
        return set(declared_signal_names)


SIGNAL_TRACKING_STRATEGIES = {
    "relevant": RelevantSignalsTracking(), "all": AllSignalsTracking(), "read-only": ReadOnlySignalsTracking(),
}


def tracked_signal_names(state: "State", declared_signal_names: set[str]) -> set[str]:
    """Which of `declared_signal_names` a turn in `state` computes:
    what its `signal-tracking-strategy` says — every declared signal, or only the
    ones referenced_signal_names finds."""
    return SIGNAL_TRACKING_STRATEGIES[state.signal_tracking_strategy].tracked_signal_names(state, declared_signal_names)


def read_signal_names(state: "State", declared_signal_names: set[str]) -> set[str]:
    return SIGNAL_TRACKING_STRATEGIES[state.signal_tracking_strategy].read_signal_names(declared_signal_names)


def referenced_signal_names(state: "State", declared_signal_names: set[str]) -> set[str]:
    referenced: set[str] = set()
    for action in state.actions:
        if action.trigger:
            referenced |= TriggerExpressionAnalyzer.signal_names(action.trigger)
        for script in (action.on_exit, action.task):
            if script:
                referenced |= TriggerExpressionAnalyzer.namespace_attrs(ast.parse(script, mode="exec"), "signal")
    return referenced & declared_signal_names


def choice_keys(state: "State") -> tuple[str, ...]:
    from .choice_namespace import expression_chains

    keys: dict[str, None] = {}
    for action in state.actions:
        if action.trigger:
            for chain in expression_chains(action.trigger):
                if len(chain) == 2 and chain[1] != "()":
                    keys.setdefault(chain[1], None)
    return tuple(keys)


def trigger_bare_names(state: "State") -> set[str]:
    """Every bare identifier any triggerable action leaving `state`
    mentions in its trigger. Unioned across those actions, which answers
    Automaton.triggers_reference exactly: the union meets a caller's name
    set if and only if some individual trigger does."""
    names: set[str] = set()
    for action in state.actions:
        if action.trigger:
            names |= TriggerExpressionAnalyzer.bare_names(action.trigger)
    return names
