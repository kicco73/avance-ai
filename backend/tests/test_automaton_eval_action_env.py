"""Automaton.eval_action_env — an action's own `env` field, evaluated like
a trigger but returning a value of any type instead of a forced boolean
cast. Unlike a trigger, a failing key here is logged, not swallowed.
"""
from __future__ import annotations

import logging

import pytest

from automaton.automaton import Action, Automaton

pytestmark = pytest.mark.contract


def _action(env=None) -> Action:
    return Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env=env)


def test_every_key_is_evaluated_independently_against_the_current_scope_and_no_env_field_yields_nothing():
    assert Automaton.eval_action_env(_action(), {}) == {}
    assert Automaton.eval_action_env(_action({"reset_counter": "True"}), {}) == {"reset_counter": True}
    assert Automaton.eval_action_env(_action({"number_of_steps": "number_of_steps + 1"}), {"number_of_steps": 3}) == {"number_of_steps": 4}
    assert Automaton.eval_action_env(_action({"mood": "'happy'", "score": "score * 2"}), {"score": 5}) == {"mood": "happy", "score": 10}


@pytest.mark.parametrize(("env", "scope"), [
    ({"total": "count + 1"}, {"count": None}),
    ({"A": "A + 1"}, {}),
    ({"broken": "1 +"}, {}),
], ids=["name-still-none", "name-missing-entirely", "malformed-expression"])
def test_a_key_that_cannot_be_evaluated_is_skipped_and_logged_rather_than_silently_no_op_d(caplog, env, scope):
    """A missing name (e.g. a typo) must be visible — unlike
    _eval_trigger's silent treatment of the same case."""
    with caplog.at_level(logging.WARNING):
        result = Automaton.eval_action_env(_action(env), scope)

    assert result == {}
    assert len(caplog.records) == 1
    assert "advance" in caplog.records[0].message


def test_one_broken_key_does_not_prevent_others_from_evaluating():
    assert Automaton.eval_action_env(_action({"broken": "1 +", "fine": "1 + 1"}), {}) == {"fine": 2}
