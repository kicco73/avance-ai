from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State

pytestmark = pytest.mark.contract


def _automaton(actions_by_state: dict[str, list[Action]]) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {"": State(key="", ui_label="", final=False, actions=[init_action])}
    for key, actions in actions_by_state.items():
        states[key] = State(key=key, ui_label=key, final=not actions, contextual_prompt="hi", actions=actions)
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=True,
        autotracking_on_ai_message=False,
    )


def test_true_when_a_trigger_references_one_of_the_given_names():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger="engagement >= 50")
    automaton = _automaton({"a": [action]})

    assert automaton.triggers_reference("a", {"engagement"}) is True


def test_false_when_no_trigger_references_any_of_the_given_names():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger="mySignal >= 50")
    automaton = _automaton({"a": [action]})

    assert automaton.triggers_reference("a", {"engagement"}) is False


def test_false_for_a_manual_only_action_with_no_trigger():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    automaton = _automaton({"a": [action]})

    assert automaton.triggers_reference("a", {"engagement"}) is False


def test_false_for_a_final_state_with_no_actions_at_all():
    automaton = _automaton({"a": []})

    assert automaton.triggers_reference("a", {"engagement"}) is False


def test_true_when_only_one_of_several_actions_references_a_name():
    action1 = Action(name="a1", ui_label="A1", ui_button="A1", target="a", trigger="x >= 1")
    action2 = Action(name="a2", ui_label="A2", ui_button="A2", target="a", trigger="engagement >= 50")
    automaton = _automaton({"a": [action1, action2]})

    assert automaton.triggers_reference("a", {"engagement"}) is True


def test_matches_against_any_name_in_a_multi_name_set():
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger="retention >= 50")
    automaton = _automaton({"a": [action]})

    assert automaton.triggers_reference("a", {"engagement", "retention", "state_stability"}) is True
