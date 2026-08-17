"""on-enter moved from being a state's own field to an action's own field
(see automaton.Action.on_enter/automaton_builder.py's _build_action/
_build_init_action) — a state reached by one action can celebrate while
the same state reached by a different action doesn't.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

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
        on-enter: celebrate
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert action.on_enter == "celebrate"


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
    """The whole point of moving it off State: two paths into the same
    state don't have to agree on whether entering it celebrates."""
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
        on-enter: celebrate
  c:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    quiet, loud = automaton.states["a"].actions
    assert quiet.on_enter is None
    assert loud.on_enter == "celebrate"


def test_a_stray_on_enter_under_a_state_is_silently_ignored():
    """on-enter is no longer a recognized state field at all — a project
    still declaring it there (e.g. not yet migrated) parses without error,
    it's just inert dead data, exactly like any other unrecognized key
    under a state."""
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
  on-enter: celebrate
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.init_action.on_enter == "celebrate"


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
        on-enter: celebrate
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    payload = automaton.get_state_payload(automaton.states["a"])
    assert "on-enter" not in payload
    assert payload["actions"][0]["on-enter"] == "celebrate"
