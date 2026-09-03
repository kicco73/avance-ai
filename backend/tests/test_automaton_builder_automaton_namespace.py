"""Build-time validation of automaton.* references (automaton_builder.py's
_actions_sanity_check): a trigger may reference
automaton.<project>.state/env.<key> only from a self-loop action.
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


def test_a_self_loop_action_may_reference_automaton_state():
    automaton = _build("automaton.otherProject.state == 'x'", target="a")
    assert automaton.states["a"].actions[0].trigger == "automaton.otherProject.state == 'x'"


def test_a_self_loop_action_may_reference_automaton_env():
    automaton = _build("automaton.otherProject.env.someKey >= 1", target="a")
    assert automaton.states["a"].actions[0].trigger == "automaton.otherProject.env.someKey >= 1"


def test_a_non_self_loop_action_referencing_automaton_state_is_rejected():
    with pytest.raises(ValueError, match="isn.t a self-loop"):
        _build("automaton.otherProject.state == 'x'", target="b")


def test_a_non_self_loop_action_referencing_automaton_env_is_rejected():
    with pytest.raises(ValueError, match="isn.t a self-loop"):
        _build("automaton.otherProject.env.someKey >= 1", target="b")


def test_a_non_self_loop_action_with_no_automaton_reference_still_builds_fine():
    automaton = _build("system.today() != None", target="b")
    assert automaton.states["a"].actions[0].target == "b"
