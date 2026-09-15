"""The `choice` trigger namespace (automaton/choice_namespace.py): a
`choice` env key's options become buttons, and the option pressed is
`choice.<key>` for the one trigger evaluation the press starts — read in
an action's trigger and env only, never rendered to the model.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from automaton.automaton_builder import AutomatonBuilder
from automaton.choice import CHOICE_BUTTON_PREFIX, ChoiceSelection, button_name, parse_button_name
from automaton.choice_namespace import ChoiceNamespace

pytestmark = pytest.mark.contract


def _project(actions_yaml: str, state_extra: str = "") -> str:
    return f"""
project:
  id: proj
env:
  slot:
    type: choice
  other:
    type: choice
  booked_slot:
    type: string
  visits:
    type: number
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
{state_extra}    actions:
{actions_yaml}
  b:
    contextual-prompt: there
"""


def _build(actions_yaml: str, state_extra: str = ""):
    return AutomatonBuilder().build({"index.yml": _project(actions_yaml, state_extra)})


BOOK = "      - name: book\n        target: b\n        trigger: \"choice.slot != ''\"\n        env:\n          booked_slot: choice.slot\n"


def test_a_trigger_and_an_env_expression_may_read_a_declared_choice_key():
    action = _build(BOOK).states["a"].actions[0]
    assert action.trigger == "choice.slot != ''"
    assert action.env == {"booked_slot": "choice.slot"}


def test_an_on_exit_assignment_may_read_a_declared_choice_key_too():
    on_exit = "picked = choice.slot\nenv.booked_slot = picked\nchat.notify('Booked', choice.slot)"
    action = _build(
        "      - name: book\n        target: b\n        trigger: \"choice.slot != ''\"\n        on-exit: |\n"
        + "".join(f"          {line}\n" for line in on_exit.split("\n"))
    ).states["a"].actions[0]
    assert action.on_exit.strip() == on_exit


@pytest.mark.parametrize(("actions_yaml", "match"), [
    ("      - name: go\n        target: b\n        trigger: \"choice.visits != ''\"\n",
     r"State a, action 'go': trigger references choice.visits — 'visits' is not an env key declared of type choice"),
    ("      - name: go\n        target: b\n        trigger: \"choice.nowhere != ''\"\n",
     r"State a, action 'go': trigger references choice.nowhere — 'nowhere' is not an env key"),
    ("      - name: go\n        target: b\n        trigger: \"choice.slot.first != ''\"\n",
     r"State a, action 'go': trigger references choice.slot.first — choice.<key> is the whole of it"),
    ("      - name: go\n        target: b\n        env:\n          booked_slot: choice.visits\n",
     r"State a, action 'go': env expression for 'booked_slot' references choice.visits"),
    ("      - name: go\n        target: b\n        on-exit: env.booked_slot = choice.visits\n",
     r"State a, action 'go': on-exit references choice.visits — 'visits' is not an env key declared of type choice"),
    ("      - name: go\n        target: b\n        trigger: \"choice.slot()\"\n",
     r"State a, action 'go': trigger calls choice.slot\(\) — choice.<key> is the option pressed, a string, not a call"),
    ("      - name: go\n        target: b\n        on-exit: env.booked_slot = choice.slot()\n",
     r"State a, action 'go': on-exit calls choice.slot\(\)"),
    ("      - name: go\n        target: b\n        task: task.send_mail(user.email, choice.slot)\n",
     r"State a, action 'go': task references choice.slot — choice.\* is read in an action's trigger, env and on-exit only"),
], ids=[
    "not-a-choice-key", "undeclared-key", "three-segments", "env-not-a-choice-key", "on-exit-not-a-choice-key",
    "called-in-trigger", "called-in-on-exit", "task",
])
def test_build_rejects_a_chain_that_is_not_exactly_a_declared_choice_key_or_sits_in_a_script(actions_yaml, match):
    with pytest.raises(ValueError, match=match):
        _build(actions_yaml)


@pytest.mark.parametrize("field_name", ["input", "output"])
def test_a_choice_key_is_never_an_input_or_an_output(field_name):
    with pytest.raises(ValueError, match=r"a choice key is never rendered to the model"):
        _build(BOOK, state_extra=f"    {field_name}:\n      - slot\n").replace(
            "  slot:\n    type: choice\n", "  slot:\n    type: choice\n    ai-definition: The slot.\n",
        )


def test_a_states_choice_keys_are_the_ones_its_triggers_read_in_order_of_first_occurrence():
    automaton = _build(
        "      - name: first\n        target: b\n        trigger: \"choice.other != '' or choice.slot != ''\"\n"
        "      - name: second\n        target: b\n        trigger: \"choice.slot != ''\"\n"
        "      - name: manual\n        target: b\n        env:\n          booked_slot: choice.slot\n"
    )
    assert automaton.states["a"].choice_keys == ("other", "slot")
    assert automaton.states["b"].choice_keys == ()


def _automaton() -> Automaton:
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={
            "": State(key="", ui_label="", final=False, actions=[init_action]),
            "a": State(key="a", ui_label="A", final=True, contextual_prompt="hi"),
        },
        general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False,
        env_keys=[EnvKey(name="slot", type="choice"), EnvKey(name="other", type="choice"), EnvKey(name="n", type="number")],
    )


def test_the_scope_holds_the_selected_option_under_its_key_and_an_empty_string_under_every_other_choice_key():
    scope = ChoiceNamespace().scope_for(_automaton(), ChoiceSelection(key="slot", option="morning"))
    assert scope.slot == "morning"
    assert scope.other == ""
    with pytest.raises(AttributeError):
        scope.n


def test_with_no_selection_every_choice_key_reads_as_an_empty_string():
    scope = ChoiceNamespace().scope_for(_automaton(), ChoiceSelection.NONE)
    assert (scope.slot, scope.other) == ("", "")


def test_identifiers_list_the_choice_keys_with_their_descriptions():
    automaton = _automaton()
    automaton.env_keys[0].ui_description = "The appointment slot."
    assert ChoiceNamespace().identifiers(automaton) == {"choice": {"slot": "The appointment slot.", "other": ""}}


def test_button_names_round_trip_through_the_current_options():
    options = {"slot": ["morning", "evening"]}
    assert button_name("slot", 1) == f"{CHOICE_BUTTON_PREFIX}slot:1"
    assert parse_button_name("choice:slot:1", options) == ChoiceSelection(key="slot", option="evening")
    assert parse_button_name("go", options) is None
    for name in ("choice:slot:2", "choice:slot:-1", "choice:slot:x", "choice:nowhere:0", "choice:"):
        with pytest.raises(ValueError):
            parse_button_name(name, options)
