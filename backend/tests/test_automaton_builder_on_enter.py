"""on-enter is an action's own field (Action.on_enter), not the state's —
a state reached by one action can celebrate while the same state
reached by a different action doesn't.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.on_enter_script import OnEnterScriptError

pytestmark = pytest.mark.contract


def test_on_enter_is_read_from_an_action_not_its_target_state():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: celebrate()
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert action.on_enter == "celebrate()"


def test_on_enter_absent_on_an_action_is_none():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert action.on_enter is None


def test_two_different_actions_landing_on_the_same_state_can_disagree_on_on_enter():
    """Two paths into the same state don't have to agree on whether
    entering it celebrates."""
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go-quiet
        target: c
      - name: go-loud
        target: c
        on-enter: celebrate()
  c:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    quiet, loud = automaton.states["a"].actions
    assert quiet.on_enter is None
    assert loud.on_enter == "celebrate()"


def test_a_stray_on_enter_under_a_state_is_silently_ignored():
    """on-enter is not a recognized state field — declaring it there
    parses without error, as inert dead data like any unrecognized key."""
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    on-enter: celebrate
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert not hasattr(automaton.states["a"], "on_enter")


def test_init_action_on_enter():
    content = """
init-action:
  target: a
  on-enter: celebrate()
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.init_action.on_enter == "celebrate()"


def test_init_action_on_enter_absent_is_none():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.init_action.on_enter is None


def test_get_state_payload_exposes_on_enter_per_outgoing_action_not_on_the_state():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: celebrate()
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    payload = automaton.get_state_payload(automaton.states["a"])
    assert "on-enter" not in payload
    assert payload["actions"][0]["on-enter"] == "celebrate()"


def test_build_rejects_an_action_with_an_invalid_on_enter_script():
    """A bare identifier (no call at all) fails the build outright, with
    the offending state/action named in the message."""
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: celebrate
  b:
    contextual-prompt: there
"""
    with pytest.raises(OnEnterScriptError, match="state 'a', action 'go'.*expected a single function call"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_rejects_an_init_action_with_an_invalid_on_enter_script():
    content = """
init-action:
  target: a
  on-enter: doStuff()
states:
  a:
    contextual-prompt: hi
"""
    with pytest.raises(OnEnterScriptError, match="init-action.*unknown function 'doStuff'"):
        AutomatonBuilder().build({"index.yml": content})
