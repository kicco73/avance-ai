"""Build-time rules for event.* references: a trigger may reference
event.<project>.state/env.<key> only from a self-loop action, and only in
those two shapes.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(trigger: str, target: str) -> object:
    content = f"""
project:
  id: test_project
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: {target}
        trigger: "{trigger}"
  b:
    contextual-prompt: there
"""
    return AutomatonBuilder().build({"index.yml": content})


def test_a_self_loop_action_may_reference_event_state():
    automaton = _build("event.otherProject.state == 'x'", target="a")
    assert automaton.states["a"].actions[0].trigger == "event.otherProject.state == 'x'"


def test_a_self_loop_action_may_reference_event_env():
    automaton = _build("event.otherProject.env.someKey >= 1", target="a")
    assert automaton.states["a"].actions[0].trigger == "event.otherProject.env.someKey >= 1"


@pytest.mark.parametrize("trigger", ["event.otherProject.state == 'x'", "event.otherProject.env.someKey >= 1"])
def test_a_non_self_loop_action_referencing_event_is_rejected(trigger):
    with pytest.raises(ValueError, match="isn.t a self-loop"):
        _build(trigger, target="b")


@pytest.mark.parametrize("trigger", ["event.otherProject.mood == 'x'", "event.otherProject == 'x'", "event.otherProject.env == 1"])
def test_anything_but_state_or_env_key_is_rejected(trigger):
    with pytest.raises(ValueError, match="only has .state and .env.<key>"):
        _build(trigger, target="a")


def test_a_non_self_loop_action_with_no_event_reference_still_builds_fine():
    automaton = _build("user.name != None", target="b")
    assert automaton.states["a"].actions[0].target == "b"
