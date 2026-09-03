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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
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
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.defer(lambda: actuator.send_mail(user.email, 'Reminder'), datetime.datetime(2030, 1, 1))
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    action = automaton.states["a"].actions[0]
    assert "actuator.defer" in action.on_enter


def test_build_still_validates_a_bad_arity_call_nested_inside_the_lambda():
    content = """
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.defer(lambda: actuator.celebrate(42), datetime.datetime(2030, 1, 1))
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="actuator.celebrate\\(\\.\\.\\.\\) takes 0 argument\\(s\\), got 1"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_rejects_the_wrong_defer_argument_count():
    content = """
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: actuator.defer(datetime.datetime(2030, 1, 1))
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="actuator.defer\\(\\.\\.\\.\\) takes 2 argument\\(s\\), got 1"):
        AutomatonBuilder().build({"index.yml": content})


def test_build_reports_the_offending_line_number_in_a_multi_line_script():
    content = """
project:
  id: proj
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


# --- actuator.defer: everything that must hold at build time -------------

def _project_with_on_enter(on_enter_line: str) -> str:
    return f"""
project:
  id: p
  ui-label: P
init-action:
  target: a
env:
  reminder_days:
    value: 3
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          {on_enter_line}
  b:
    contextual-prompt: there
"""


@pytest.mark.parametrize("when", [
    "datetime.datetime(2030, 1, 1)",
    "datetime.datetime(2030, 1, 1, 9, 0, tzinfo=datetime.timezone.utc)",
    "datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)",
    "datetime.datetime.now() - datetime.timedelta(hours=2)",
    "datetime.timedelta(minutes=5) + datetime.datetime.now()",
    "datetime.datetime.now() + datetime.timedelta(days=1) + datetime.timedelta(hours=env.reminder_days)",
    "datetime.datetime.now() + datetime.timedelta(days=env.reminder_days)",
    "datetime.datetime.now() + datetime.timedelta(days=signal.mood)",
])
def test_defer_accepts_a_when_of_datetime_shape(when):
    content = _project_with_on_enter(f"actuator.defer(lambda: actuator.celebrate(), {when})").replace(
        "env:\n", "signals:\n  mood:\n    definition: mood\nenv:\n"
    )
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert "actuator.defer" in automaton.states["a"].actions[0].on_enter


@pytest.mark.parametrize("when", [
    "env.reminder_days",
    "'2030-01-01'",
    "user.created_at",
    "datetime.timedelta(days=1)",
    "datetime.datetime.now() - datetime.datetime(2030, 1, 1)",
])
def test_defer_rejects_a_when_that_is_not_a_datetime_by_shape(when):
    content = _project_with_on_enter(f"actuator.defer(lambda: actuator.celebrate(), {when})")
    with pytest.raises(ValueError, match="`when` must be a datetime"):
        AutomatonBuilder().build({"index.yml": content})


def test_defer_rejects_a_string_inside_timedelta():
    content = _project_with_on_enter(
        "actuator.defer(lambda: actuator.celebrate(), datetime.datetime.now() + datetime.timedelta(days=user.name))"
    )
    with pytest.raises(ValueError, match="timedelta\\(\\) takes numbers"):
        AutomatonBuilder().build({"index.yml": content})


@pytest.mark.parametrize("act", ["actuator.celebrate", "actuator.celebrate()", "user.name", "lambda x: actuator.celebrate()"])
def test_defer_rejects_a_first_argument_that_is_not_a_zero_argument_lambda(act):
    content = _project_with_on_enter(f"actuator.defer({act}, datetime.datetime(2030, 1, 1))")
    with pytest.raises(ValueError, match="lambda"):
        AutomatonBuilder().build({"index.yml": content})


def test_on_enter_may_not_reference_session_even_outside_a_defer():
    """`session.*` is not part of the actuator scope at all (see
    IdentifierRegistry.ACTUATOR_SCOPE_EXCLUDES): an on-enter line runs
    inside a session today, but a deferred one won't — one scope, no
    special case."""
    content = _project_with_on_enter("actuator.notify('Hi', session.number_of_user_sessions())")
    with pytest.raises(ValueError, match="undefined name\\(s\\): session.number_of_user_sessions"):
        AutomatonBuilder().build({"index.yml": content})


def test_on_enter_may_not_reference_session_metric_either():
    content = _project_with_on_enter("actuator.defer(lambda: actuator.notify('Hi', session.metric.engagement()), datetime.datetime(2030, 1, 1))")
    with pytest.raises(ValueError, match="undefined name\\(s\\): session.metric.engagement"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_still_sees_session():
    content = _project_with_on_enter("actuator.celebrate()").replace(
        "        target: b\n", "        target: b\n        trigger: session.number_of_user_sessions() >= 1\n"
    )
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger


def test_defer_accepts_actuator_prompt_inside_the_lambda():
    """actuator.prompt is a fully isolated model call — no session/chat
    history involved — so it's as usable inside a deferred lambda as any
    other actuator."""
    content = _project_with_on_enter(
        "actuator.defer(lambda: actuator.notify('Later', actuator.prompt('Recap')), datetime.datetime(2030, 1, 1))"
    )
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert "actuator.prompt" in automaton.states["a"].actions[0].on_enter


def test_defer_accepts_actuator_prompt_evaluated_now_as_an_argument_of_when_free_code():
    """The composition that does work: prompt now, defer the result."""
    content = _project_with_on_enter(
        "actuator.defer(lambda: actuator.notify('Later', env.reminder_days), datetime.datetime(2030, 1, 1))\n"
        "          actuator.notify('Now', actuator.prompt('Recap'))"
    )
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert "actuator.prompt" in automaton.states["a"].actions[0].on_enter
