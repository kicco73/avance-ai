"""on-enter is an action's own field (Action.on_enter), not the state's —
a state reached by one action can celebrate while the same state
reached by a different action doesn't. Since the actuator field merged
into on-enter, its grammar is the same namespaced actuator.<name>(...)
call — one per non-blank line — that the standalone `actuator:` field
used to validate (see AutomatonBuilder._validate_on_enter).
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
        on-enter: actuator.celebrate()
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert action.on_enter == "actuator.celebrate()"


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
        on-enter: actuator.celebrate()
  c:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    quiet, loud = automaton.states["a"].actions
    assert quiet.on_enter is None
    assert loud.on_enter == "actuator.celebrate()"


def test_a_stray_on_enter_under_a_state_is_silently_ignored():
    """on-enter is not a recognized state field — declaring it there
    parses without error, as inert dead data like any unrecognized key."""
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    on-enter: actuator.celebrate()
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert not hasattr(automaton.states["a"], "on_enter")


def test_on_enter_accepts_multiple_actuator_calls_one_per_line():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.celebrate()
          actuator.notify('Nice!', 'You reached **state B**.')
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert action.on_enter.splitlines() == [
        "actuator.celebrate()", "actuator.notify('Nice!', 'You reached **state B**.')",
    ]


def test_init_action_on_enter():
    content = """
init-action:
  target: a
  on-enter: actuator.celebrate()
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.init_action.on_enter == "actuator.celebrate()"


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
        on-enter: actuator.celebrate()
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    payload = automaton.get_state_payload(automaton.states["a"])
    assert "on-enter" not in payload
    assert payload["actions"][0]["on-enter"] == "actuator.celebrate()"


def test_build_rejects_an_action_with_a_bare_unnamespaced_call():
    """A bare (non-actuator) call is just an undefined bare name — the
    same "undefined name(s)" error any other unknown identifier gets."""
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
    with pytest.raises(ValueError, match="State a, action 'go'.*references undefined name\\(s\\): celebrate"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_rejects_an_unknown_actuator_method():
    content = """
init-action:
  target: a
  on-enter: actuator.doStuff()
states:
  a:
    contextual-prompt: hi
"""
    with pytest.raises(ValueError, match="init-action.*references undefined name\\(s\\): actuator.doStuff"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_rejects_the_wrong_actuator_argument_count():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: actuator.celebrate(42)
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="actuator.celebrate\\(\\.\\.\\.\\) takes 0 argument\\(s\\), got 1"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_accepts_actuator_defer_with_a_lambda_argument():
    # A bare, unquoted "lambda: ..." on one YAML line misparses (YAML
    # reads that colon as its own mapping separator) — the block scalar
    # form (or an explicitly quoted line) is required in a real index.yml.
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.defer(lambda: actuator.send_mail(user.email, 'Reminder'), system.today)
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert "actuator.defer" in action.on_enter


def test_build_still_validates_a_bad_arity_call_nested_inside_the_lambda():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.defer(lambda: actuator.celebrate(42), system.today)
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="actuator.celebrate\\(\\.\\.\\.\\) takes 0 argument\\(s\\), got 1"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_rejects_the_wrong_defer_argument_count():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: actuator.defer(system.today)
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="actuator.defer\\(\\.\\.\\.\\) takes 2 argument\\(s\\), got 1"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_reports_the_offending_line_number_in_a_multi_line_script():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.celebrate()
          actuator.doStuff()
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="on-enter line 2.*references undefined name\\(s\\): actuator.doStuff"):
        AutomatonBuilder().build({"index.yml": content})
