"""task is an action's own field (Action.task), not the state's —
a state reached by one action can send mail while the same state
reached by a different action doesn't. Since the actuator field merged
into task, its grammar is the same namespaced task.<name>(...)
call — one per non-blank line — that the standalone `actuator:` field
used to validate (see AutomatonValidator.validate_task). task's own
namespace only carries send_mail/whatsapp/defer/prompt now —
celebrate/notify/show moved to chat, reachable only from on-exit (see
test_automaton_builder_on_exit.py)."""
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


def _go(task_yaml: str = "") -> str:
    return _two_states("      - name: go\n        target: b\n" + task_yaml)


def test_task_belongs_to_the_action_not_its_target_state_and_is_none_when_absent():
    """Two paths into the same state don't have to agree on whether
    entering it emails; a stray task under a state is inert dead
    data like any unrecognized key."""
    quiet, loud = _build(_two_states(
        "      - name: go-quiet\n        target: b\n      - name: go-loud\n        target: b\n"
        "        task: task.send_mail(user.email, 'hi')\n"
    )).states["a"].actions
    assert quiet.task is None
    assert loud.task == "task.send_mail(user.email, 'hi')"

    stray = _build("""
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    task: task.send_mail(user.email, 'hi')
""")
    assert not hasattr(stray.states["a"], "task")

    automaton = _build(_go("        task: task.send_mail(user.email, 'hi')\n"))
    payload = automaton.get_state_payload(automaton.states["a"])
    assert "task" not in payload
    assert payload["actions"][0]["task"] == "task.send_mail(user.email, 'hi')"


def test_task_accepts_several_task_calls_one_per_line_on_actions_and_the_init_action_alike():
    multi = _build(_go(
        "        task: |\n          task.send_mail(user.email, 'hi')\n"
        "          task.whatsapp('34600000001', 'You reached **state B**.')\n"
    ))
    assert multi.states["a"].actions[0].task.splitlines() == [
        "task.send_mail(user.email, 'hi')", "task.whatsapp('34600000001', 'You reached **state B**.')",
    ]

    assert _build(_two_states(
        "      - name: go\n        target: b\n", init_extra="  task: task.send_mail(user.email, 'hi')\n",
    )).init_action.task == "task.send_mail(user.email, 'hi')"
    assert _build(_go()).init_action.task is None


@pytest.mark.parametrize(("task", "match"), [
    ("celebrate()", r"State a, action 'go'.*references undefined name\(s\): celebrate"),
    ("task.doStuff()", r"references undefined name\(s\): task.doStuff"),
    ("task.whatsapp('34600000001')", r"task.whatsapp\(\.\.\.\) takes 2 argument\(s\), got 1"),
    ("task.defer(datetime.datetime(2030, 1, 1))", r"task.defer\(\.\.\.\) takes 2 argument\(s\), got 1"),
    ("|\n          task.send_mail(user.email, 'hi')\n          task.doStuff()", r"task line 2.*references undefined name\(s\): task.doStuff"),
    (
        "|\n          task.defer(lambda: task.whatsapp('34600000001'), datetime.datetime(2030, 1, 1))",
        r"task.whatsapp\(\.\.\.\) takes 2 argument\(s\), got 1",
    ),
])
def test_build_rejects_bare_unknown_or_wrongly_called_task_calls_even_nested_in_a_lambda_reporting_the_line(task, match):
    """A bare (non-task) call is just an undefined bare name — the
    same "undefined name(s)" error any other unknown identifier gets."""
    with pytest.raises(ValueError, match=match):
        _build(_go(f"        task: {task}\n"))


def test_build_rejects_an_unknown_task_method_on_the_init_action_too():
    with pytest.raises(ValueError, match=r"init-action.*references undefined name\(s\): task.doStuff"):
        _build(_two_states("      - name: go\n        target: b\n", init_extra="  task: task.doStuff()\n"))


def test_build_rejects_a_chat_call_inside_task_since_chat_is_on_exit_only():
    """The flip side of test_automaton_builder_on_exit.py's own
    rejection of task.* inside on-exit: `task:` can't see `chat.*` either."""
    with pytest.raises(ValueError, match=r"references undefined name\(s\): chat.celebrate"):
        _build(_go("        task: chat.celebrate()\n"))


# --- task.defer: everything that must hold at build time -------------

def _project_with_task(task_line: str) -> str:
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
        task: |
          {task_line}
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
    content = _project_with_task(f"task.defer(lambda: task.send_mail(user.email, 'hi'), {when})").replace(
        "env:\n", "signals:\n  mood:\n    definition: mood\nenv:\n"
    )
    assert "task.defer" in _build(content).states["a"].actions[0].task


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
        _build(_project_with_task(f"task.defer(lambda: task.send_mail(user.email, 'hi'), {when})"))


@pytest.mark.parametrize("act", ["task.send_mail", "task.send_mail(user.email, 'hi')", "user.name", "lambda x: task.send_mail(user.email, 'hi')"])
def test_defer_rejects_a_first_argument_that_is_not_a_zero_argument_lambda(act):
    with pytest.raises(ValueError, match="lambda"):
        _build(_project_with_task(f"task.defer({act}, datetime.datetime(2030, 1, 1))"))


def test_task_never_sees_session_deferred_or_not_while_a_trigger_still_does():
    """`session.*` is not part of the task scope at all (see
    IdentifierRegistry.TASK_SCOPE_EXCLUDES): a task line runs
    inside a session today, but a deferred one won't — one scope, no
    special case."""
    with pytest.raises(ValueError, match=r"undefined name\(s\): session.number_of_user_sessions"):
        _build(_project_with_task("task.send_mail(user.email, session.number_of_user_sessions())"))
    with pytest.raises(ValueError, match=r"undefined name\(s\): session.metric.engagement"):
        _build(_project_with_task(
            "task.defer(lambda: task.send_mail(user.email, session.metric.engagement()), datetime.datetime(2030, 1, 1))"
        ))

    content = _project_with_task("task.send_mail(user.email, 'hi')").replace(
        "        target: b\n", "        target: b\n        trigger: session.number_of_user_sessions() >= 1\n"
    )
    assert _build(content).states["a"].actions[0].trigger


def test_defer_accepts_task_prompt_inside_the_lambda_or_evaluated_now_as_an_argument():
    """task.prompt is a fully isolated model call — no session/chat
    history involved — so it's as usable inside a deferred lambda as any
    other task call; prompting now and deferring the result works too."""
    inside = _build(_project_with_task(
        "task.defer(lambda: task.send_mail(user.email, task.prompt('Recap')), datetime.datetime(2030, 1, 1))"
    ))
    assert "task.prompt" in inside.states["a"].actions[0].task

    now = _build(_project_with_task(
        "task.defer(lambda: task.send_mail(user.email, env.reminder_days), datetime.datetime(2030, 1, 1))\n"
        "          task.send_mail(user.email, task.prompt('Recap'))"
    ))
    assert "task.prompt" in now.states["a"].actions[0].task
