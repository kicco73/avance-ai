"""on-exit is an action's own field (Action.on_exit), not the state's —
same "which action, not which destination state" ownership as task
(see test_automaton_builder_task.py). Its own grammar is mixed: every
line is either an `env.<key> = expr` assignment writing an already
declared env key (it shares task's own statement splitting,
TriggerExpressionAnalyzer.task_statements, but not its assignment
shape — on_exit_assignment requires the explicit `env.` target, since
on-exit has no local-variable concept of its own, only env writes — the
future replacement for the declarative `env:` map) or a bare
`chat.<method>(...)` call — on-exit's own side effect namespace (see
AutomatonValidator.validate_on_exit). `task:`'s own namespace
(send_mail/whatsapp/defer/prompt) stays off-limits here, same as
`chat.*` stays off-limits inside `task:` (see test_automaton_builder_task.py's
own test_build_rejects_a_chat_call_inside_task_since_chat_is_on_exit_only)."""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(content: str):
    return AutomatonBuilder().build({"index.yml": content})


def _project(actions_yaml: str, init_extra: str = "", env_yaml: str = "") -> str:
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
env:
  counter:
    value: 0
  flight:
    value: ""
{env_yaml}
"""


def _go(on_exit_yaml: str = "") -> str:
    return _project("      - name: go\n        target: b\n" + on_exit_yaml)


def test_on_exit_belongs_to_the_action_not_its_target_state_and_is_none_when_absent():
    quiet, loud = _build(_project(
        "      - name: go-quiet\n        target: b\n"
        "      - name: go-loud\n        target: b\n        on-exit: env.counter = 1\n"
    )).states["a"].actions
    assert quiet.on_exit is None
    assert loud.on_exit == "env.counter = 1"

    automaton = _build(_go("        on-exit: env.counter = 1\n"))
    payload = automaton.get_state_payload(automaton.states["a"])
    assert "on-exit" not in payload
    assert payload["actions"][0]["on-exit"] == "env.counter = 1"


def test_on_exit_accepts_several_assignments_one_per_line_on_actions_and_the_init_action_alike():
    multi = _build(_go("        on-exit: |\n          env.counter = env.counter + 1\n          env.flight = 'VY123'\n"))
    assert multi.states["a"].actions[0].on_exit.splitlines() == ["env.counter = env.counter + 1", "env.flight = 'VY123'"]

    assert _build(_project(
        "      - name: go\n        target: b\n", init_extra="  on-exit: env.counter = 1\n",
    )).init_action.on_exit == "env.counter = 1"
    assert _build(_go()).init_action.on_exit is None


def test_on_exit_accepts_a_mix_of_env_assignments_and_bare_chat_calls():
    multi = _build(_go(
        "        on-exit: |\n          env.counter = env.counter + 1\n          chat.celebrate()\n"
        "          chat.notify('Nice!', 'You reached **state B**.')\n"
    ))
    assert multi.states["a"].actions[0].on_exit.splitlines() == [
        "env.counter = env.counter + 1", "chat.celebrate()", "chat.notify('Nice!', 'You reached **state B**.')",
    ]


def test_on_exit_accepts_chat_switch_to_human_and_switch_to_ai():
    assert _build(_go("        on-exit: chat.switch_to_human(user.email)\n")).states["a"].actions[0].on_exit
    assert _build(_go("        on-exit: chat.switch_to_ai()\n")).states["a"].actions[0].on_exit


@pytest.mark.parametrize(("on_exit", "match"), [
    ("counter = 1", r"on-exit only supports 'env.<key> = expr' assignments"),
    ("counter", r"on-exit only supports 'env.<key> = expr' assignments"),
    ("task.send_mail(user.email, 'hi')", r"on-exit only supports 'env.<key> = expr' assignments"),
    ("chat.celebrate(1)", r"chat.celebrate\(\.\.\.\) takes 0 argument\(s\), got 1"),
    ("env.unknown_key = 1", r"env key 'unknown_key' is not declared"),
    ("env.counter = user.name", r"is a string, but 'counter' was declared as a number"),
    ("|\n          env.counter = 1\n          env.unknown_key = 2", r"on-exit line 2.*env key 'unknown_key' is not declared"),
])
def test_build_rejects_non_assignment_undeclared_mistyped_or_wrongly_called_on_exit_lines(on_exit, match):
    with pytest.raises(ValueError, match=match):
        _build(_go(f"        on-exit: {on_exit}\n"))


def test_build_rejects_a_task_call_from_on_exit():
    """task.*'s own send_mail/whatsapp/defer/prompt stay off-limits from
    on-exit — that's task's own job, never on-exit's."""
    with pytest.raises(ValueError, match=r"references undefined name\(s\): task.send_mail"):
        _build(_go("        on-exit: env.counter = 1 if task.send_mail(user.email, 'hi') else 0\n"))


def test_build_rejects_an_undeclared_env_key_on_the_init_action_too():
    with pytest.raises(ValueError, match=r"init-action.*env key 'unknown_key' is not declared"):
        _build(_project("      - name: go\n        target: b\n", init_extra="  on-exit: env.unknown_key = 1\n"))


def test_on_exit_may_reference_signal_env_and_chat_but_not_task():
    """on-exit's own registry view (IdentifierRegistry.for_on_exit) —
    session/env/signal/chat namespaces resolve, task.* doesn't exist here."""
    content = _project(
        "      - name: go\n        target: b\n        on-exit: env.counter = signal.mood\n",
        env_yaml="",
    ).replace("project:\n  id: proj\n", "project:\n  id: proj\nsignals:\n  mood:\n    definition: mood\n")
    assert _build(content).states["a"].actions[0].on_exit == "env.counter = signal.mood"

    assert _build(_go("        on-exit: chat.celebrate()\n")).states["a"].actions[0].on_exit == "chat.celebrate()"

    with pytest.raises(ValueError, match=r"references undefined name\(s\): task.prompt"):
        _build(_go("        on-exit: env.counter = 1 if task.prompt('hi') else 0\n"))


def test_declared_env_key_names_and_triggerable_signal_names_include_on_exit_writes():
    content = _project(
        "      - name: go\n        target: b\n        trigger: \"1\"\n        on-exit: env.counter = signal.mood\n",
    ).replace("project:\n  id: proj\n", "project:\n  id: proj\nsignals:\n  mood:\n    definition: mood\n")
    automaton = _build(content)
    assert "counter" in automaton.declared_env_key_names()
    assert "mood" in automaton.triggerable_signal_names("a")


def test_eval_action_on_exit_evaluates_assignments_against_scope_and_skips_bad_ones():
    """Runtime robustness — a bad expression (a stale env reference no
    longer valid at the revision this scope was built from) is logged
    and skipped, same eval_action_env contract, never raised."""
    from automaton.automaton import Action, Automaton

    action = Action(
        name="go", ui_label="go", ui_button="go", target="b",
        on_exit="env.counter = env.counter + 1\nenv.flight = env.does_not_exist",
    )
    updates, chat_snippets = Automaton.eval_action_on_exit(action, {"env": {"counter": 5}})
    assert updates == {"counter": 6}
    assert chat_snippets is None


def test_eval_action_on_exit_collects_chat_snippets_alongside_env_updates():
    """A mixed script's env.<key> writes and chat.* calls are both
    evaluated in one pass — the joined snippet text is what
    TrackingEngine.apply_action_env pushes over the websocket."""
    from automaton.automaton import Action, Automaton
    from automaton.scope import EvaluationScope
    from tracking.actuators.chat_namespace import FakeChatNamespace

    action = Action(
        name="go", ui_label="go", ui_button="go", target="b",
        on_exit="env.counter = env.counter + 1\nchat.celebrate()\nchat.notify('Nice!', 'Done.')",
    )
    scope = EvaluationScope({"env": {"counter": 5}, "chat": FakeChatNamespace()}, automaton=None, state_key="a")
    updates, chat_snippets = Automaton.eval_action_on_exit(action, scope)
    assert updates == {"counter": 6}
    assert chat_snippets == 'celebrate()\nnotify("Nice!", "Done.")'
