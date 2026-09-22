"""Automaton.eval_action_env — an action's own `env` field, evaluated like
a trigger but returning a value of any type instead of a forced boolean
cast. Unlike a trigger, a failing key here is logged, not swallowed; so
is a value that is not of the key's declared type. A key that fails to
evaluate is also returned in the second element, (label, exception)
pairs — TaskOutcome.failures' own shape — so a caller can surface it
rather than let the log line be the only trace of it.
"""
from __future__ import annotations

import logging

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State

pytestmark = pytest.mark.contract


def _action(env=None) -> Action:
    return Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env=env)


def _automaton(env_keys: list[EnvKey] | None = None) -> Automaton:
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={
            "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
            "a": State(input_processor="ai", key="a", ui_label="A", final=True, contextual_prompt="hi"),
        },
        general_prompt="",
        signals=[],
        general_attachments=(),
        autotracking_on_ai_message=False,
        env_keys=env_keys,
    )


def test_every_key_is_evaluated_independently_against_the_current_scope_and_no_env_field_yields_nothing():
    automaton = _automaton()
    assert automaton.eval_action_env(_action(), {}) == ({}, ())
    assert automaton.eval_action_env(_action({"reset_counter": "True"}), {}) == ({"reset_counter": True}, ())
    assert automaton.eval_action_env(_action({"number_of_steps": "number_of_steps + 1"}), {"number_of_steps": 3}) == ({"number_of_steps": 4}, ())
    assert automaton.eval_action_env(_action({"mood": "'happy'", "score": "score * 2"}), {"score": 5}) == ({"mood": "happy", "score": 10}, ())


def test_len_is_available_to_an_env_expression():
    automaton = _automaton()
    assert automaton.eval_action_env(_action({"count": "len(names)"}), {"names": ["a", "b", "c"]}) == ({"count": 3}, ())


@pytest.mark.parametrize(("env", "scope"), [
    ({"total": "count + 1"}, {"count": None}),
    ({"A": "A + 1"}, {}),
    ({"broken": "1 +"}, {}),
], ids=["name-still-none", "name-missing-entirely", "malformed-expression"])
def test_a_key_that_cannot_be_evaluated_is_skipped_and_logged_rather_than_silently_no_op_d(caplog, env, scope):
    """A missing name (e.g. a typo) must be visible — unlike
    _eval_trigger's silent treatment of the same case — and reported
    back to the caller, not just logged."""
    with caplog.at_level(logging.WARNING):
        result, failures = _automaton().eval_action_env(_action(env), scope)

    assert result == {}
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert "advance" in caplog.records[0].message
    assert len(failures) == 1
    key = next(iter(env))
    assert failures[0][0].startswith(f"{key}:")


def test_one_broken_key_does_not_prevent_others_from_evaluating():
    result, failures = _automaton().eval_action_env(_action({"broken": "1 +", "fine": "1 + 1"}), {})
    assert result == {"fine": 2}
    assert len(failures) == 1 and failures[0][0].startswith("broken:")


TYPED_KEYS = [
    EnvKey(name="count", type="number"), EnvKey(name="name", type="string"),
    EnvKey(name="flag", type="bool"), EnvKey(name="slot", type="list"),
]


@pytest.mark.parametrize(("env", "expected"), [
    ({"count": "3", "name": "'x'", "flag": "True", "slot": "['a', 'b']"}, {"count": 3, "name": "x", "flag": True, "slot": ["a", "b"]}),
    ({"count": "1.5"}, {"count": 1.5}),
    ({"slot": "[]"}, {"slot": []}),
], ids=["every-type", "float-is-a-number", "empty-list"])
def test_a_value_of_the_declared_type_is_written(env, expected):
    assert _automaton(TYPED_KEYS).eval_action_env(_action(env), {}) == (expected, ())


@pytest.mark.parametrize(("env", "expected", "logged"), [
    ({"count": "'three'", "name": "'x'"}, {"name": "x"}, "count"),
    ({"count": "True", "flag": "False"}, {"flag": False}, "count"),
    ({"name": "3", "count": "3"}, {"count": 3}, "name"),
    ({"flag": "1", "name": "'x'"}, {"name": "x"}, "flag"),
    ({"slot": "['a', 2]", "count": "0"}, {"count": 0}, "slot"),
    ({"slot": "'a'", "count": "0"}, {"count": 0}, "slot"),
    ({"count": "None", "flag": "True"}, {"flag": True}, "count"),
], ids=[
    "string-into-number", "bool-into-number", "number-into-string", "number-into-bool",
    "non-string-option-into-list", "string-into-list", "none-into-number",
])
def test_a_value_outside_the_declared_type_is_discarded_and_logged_while_the_other_keys_are_written(
    caplog, env, expected, logged
):
    with caplog.at_level(logging.WARNING):
        result, failures = _automaton(TYPED_KEYS).eval_action_env(_action(env), {})

    assert result == expected
    assert failures == ()
    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert "advance" in message and f"'{logged}'" in message
    assert {"count": "number", "name": "string", "flag": "bool", "slot": "list"}[logged] in message
