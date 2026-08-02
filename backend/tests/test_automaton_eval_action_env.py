"""Automaton.eval_action_env — an action's own `env` field (see
automaton_builder.py's _build_action), evaluated the same way a trigger
is (see Automaton._eval_trigger) but returning a value of any type
instead of forcing a boolean cast. Unlike a trigger, a failing key here
is never silently swallowed — see test_*_is_logged_not_silent below.
"""
from __future__ import annotations

import logging

from automaton.automaton import Action, Automaton


def test_no_env_field_returns_an_empty_dict():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")

    assert Automaton.eval_action_env(action, {}) == {}


def test_a_literal_expression_needs_no_referenced_names():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env={"reset_counter": "True"})

    assert Automaton.eval_action_env(action, {}) == {"reset_counter": True}


def test_an_expression_referencing_the_current_scope():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env={"number_of_steps": "number_of_steps + 1"})

    assert Automaton.eval_action_env(action, {"number_of_steps": 3}) == {"number_of_steps": 4}


def test_multiple_keys_evaluated_independently():
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="a",
        env={"mood": "'happy'", "score": "score * 2"},
    )

    assert Automaton.eval_action_env(action, {"score": 5}) == {"mood": "happy", "score": 10}


def test_a_referenced_name_that_is_still_none_does_not_raise_but_is_skipped(caplog):
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env={"total": "count + 1"})

    with caplog.at_level(logging.WARNING):
        result = Automaton.eval_action_env(action, {"count": None})

    assert result == {}


def test_a_referenced_name_missing_entirely_is_logged_not_silent(caplog):
    """The exact scenario that must be visible, not silently no-op'd:
    `A: A + 1` where `A` doesn't exist in env yet (a typo, or a
    free-form key never set) — see Automaton.eval_action_env's own
    docstring for why this diverges from _eval_trigger's silent
    treatment of an unresolved name."""
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env={"A": "A + 1"})

    with caplog.at_level(logging.WARNING):
        result = Automaton.eval_action_env(action, {})

    assert result == {}
    assert len(caplog.records) == 1
    assert "'A'" in caplog.records[0].message
    assert "advance" in caplog.records[0].message


def test_a_malformed_expression_is_logged_not_silent(caplog):
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", env={"broken": "1 +"})

    with caplog.at_level(logging.WARNING):
        result = Automaton.eval_action_env(action, {})

    assert result == {}
    assert len(caplog.records) == 1


def test_one_broken_key_does_not_prevent_others_from_evaluating():
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="a",
        env={"broken": "1 +", "fine": "1 + 1"},
    )

    assert Automaton.eval_action_env(action, {}) == {"fine": 2}
