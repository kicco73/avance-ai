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


def _build(content: str):
    return AutomatonBuilder().build({"index.yml": content})


def _two_states(actions_yaml: str, init_extra: str = "") -> str:
    return f"""
project:
  id: proj
init-action:
  target: a
{init_extra}states:
  a:
    contextual-prompt: hi
    actions:
{actions_yaml}
  b:
    contextual-prompt: there
"""


def _go(on_enter_yaml: str = "") -> str:
    return _two_states("      - name: go\n        target: b\n" + on_enter_yaml)


def test_on_enter_belongs_to_the_action_not_its_target_state_and_is_none_when_absent():
    """Two paths into the same state don't have to agree on whether
    entering it celebrates; a stray on-enter under a state is inert dead
    data like any unrecognized key."""
    quiet, loud = _build(_two_states(
        "      - name: go-quiet\n        target: b\n      - name: go-loud\n        target: b\n        on-enter: actuator.celebrate()\n"
    )).states["a"].actions
    assert quiet.on_enter is None
    assert loud.on_enter == "actuator.celebrate()"

    stray = _build("""
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    on-enter: actuator.celebrate()
""")
    assert not hasattr(stray.states["a"], "on_enter")

    automaton = _build(_go("        on-enter: actuator.celebrate()\n"))
    payload = automaton.get_state_payload(automaton.states["a"])
    assert "on-enter" not in payload
    assert payload["actions"][0]["on-enter"] == "actuator.celebrate()"


def test_on_enter_accepts_several_actuator_calls_one_per_line_on_actions_and_the_init_action_alike():
    multi = _build(_go("        on-enter: |\n          actuator.celebrate()\n          actuator.notify('Nice!', 'You reached **state B**.')\n"))
    assert multi.states["a"].actions[0].on_enter.splitlines() == [
        "actuator.celebrate()", "actuator.notify('Nice!', 'You reached **state B**.')",
    ]

    assert _build(_two_states("      - name: go\n        target: b\n", init_extra="  on-enter: actuator.celebrate()\n")).init_action.on_enter == "actuator.celebrate()"
    assert _build(_go()).init_action.on_enter is None


@pytest.mark.parametrize(("on_enter", "match"), [
    ("celebrate()", r"State a, action 'go'.*references undefined name\(s\): celebrate"),
    ("actuator.doStuff()", r"references undefined name\(s\): actuator.doStuff"),
    ("actuator.celebrate(42)", r"actuator.celebrate\(\.\.\.\) takes 0 argument\(s\), got 1"),
    ("actuator.defer(datetime.datetime(2030, 1, 1))", r"actuator.defer\(\.\.\.\) takes 2 argument\(s\), got 1"),
    ("|\n          actuator.celebrate()\n          actuator.doStuff()", r"on-enter line 2.*references undefined name\(s\): actuator.doStuff"),
    ("|\n          actuator.defer(lambda: actuator.celebrate(42), datetime.datetime(2030, 1, 1))", r"actuator.celebrate\(\.\.\.\) takes 0 argument\(s\), got 1"),
])
def test_build_rejects_bare_unknown_or_wrongly_called_actuators_even_nested_in_a_lambda_reporting_the_line(on_enter, match):
    """A bare (non-actuator) call is just an undefined bare name — the
    same "undefined name(s)" error any other unknown identifier gets."""
    with pytest.raises(ValueError, match=match):
        _build(_go(f"        on-enter: {on_enter}\n"))


def test_build_rejects_an_unknown_actuator_method_on_the_init_action_too():
    with pytest.raises(ValueError, match=r"init-action.*references undefined name\(s\): actuator.doStuff"):
        _build(_two_states("      - name: go\n        target: b\n", init_extra="  on-enter: actuator.doStuff()\n"))


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
    # A bare, unquoted "lambda: ..." on one YAML line misparses (YAML
    # reads that colon as its own mapping separator) — the block scalar
    # form (or an explicitly quoted line) is required in a real index.yml.
    content = _project_with_on_enter(f"actuator.defer(lambda: actuator.celebrate(), {when})").replace(
        "env:\n", "signals:\n  mood:\n    definition: mood\nenv:\n"
    )
    assert "actuator.defer" in _build(content).states["a"].actions[0].on_enter


@pytest.mark.parametrize(("when", "match"), [
    ("env.reminder_days", "`when` must be a datetime"),
    ("'2030-01-01'", "`when` must be a datetime"),
    ("user.created_at", "`when` must be a datetime"),
    ("datetime.timedelta(days=1)", "`when` must be a datetime"),
    ("datetime.datetime.now() - datetime.datetime(2030, 1, 1)", "`when` must be a datetime"),
    ("datetime.datetime.now() + datetime.timedelta(days=user.name)", r"timedelta\(\) takes numbers"),
])
def test_defer_rejects_a_when_that_is_not_a_datetime_by_shape_or_a_string_inside_timedelta(when, match):
    with pytest.raises(ValueError, match=match):
        _build(_project_with_on_enter(f"actuator.defer(lambda: actuator.celebrate(), {when})"))


@pytest.mark.parametrize("act", ["actuator.celebrate", "actuator.celebrate()", "user.name", "lambda x: actuator.celebrate()"])
def test_defer_rejects_a_first_argument_that_is_not_a_zero_argument_lambda(act):
    with pytest.raises(ValueError, match="lambda"):
        _build(_project_with_on_enter(f"actuator.defer({act}, datetime.datetime(2030, 1, 1))"))


def test_on_enter_never_sees_session_deferred_or_not_while_a_trigger_still_does():
    """`session.*` is not part of the actuator scope at all (see
    IdentifierRegistry.ACTUATOR_SCOPE_EXCLUDES): an on-enter line runs
    inside a session today, but a deferred one won't — one scope, no
    special case."""
    with pytest.raises(ValueError, match=r"undefined name\(s\): session.number_of_user_sessions"):
        _build(_project_with_on_enter("actuator.notify('Hi', session.number_of_user_sessions())"))
    with pytest.raises(ValueError, match=r"undefined name\(s\): session.metric.engagement"):
        _build(_project_with_on_enter("actuator.defer(lambda: actuator.notify('Hi', session.metric.engagement()), datetime.datetime(2030, 1, 1))"))

    content = _project_with_on_enter("actuator.celebrate()").replace(
        "        target: b\n", "        target: b\n        trigger: session.number_of_user_sessions() >= 1\n"
    )
    assert _build(content).states["a"].actions[0].trigger


def test_defer_accepts_actuator_prompt_inside_the_lambda_or_evaluated_now_as_an_argument():
    """actuator.prompt is a fully isolated model call — no session/chat
    history involved — so it's as usable inside a deferred lambda as any
    other actuator; prompting now and deferring the result works too."""
    inside = _build(_project_with_on_enter(
        "actuator.defer(lambda: actuator.notify('Later', actuator.prompt('Recap')), datetime.datetime(2030, 1, 1))"
    ))
    assert "actuator.prompt" in inside.states["a"].actions[0].on_enter

    now = _build(_project_with_on_enter(
        "actuator.defer(lambda: actuator.notify('Later', env.reminder_days), datetime.datetime(2030, 1, 1))\n"
        "          actuator.notify('Now', actuator.prompt('Recap'))"
    ))
    assert "actuator.prompt" in now.states["a"].actions[0].on_enter
