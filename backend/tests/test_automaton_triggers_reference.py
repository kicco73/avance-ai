from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State

pytestmark = pytest.mark.contract


def _automaton(*triggers: str | None) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    actions = [
        Action(name=f"a{i}", ui_label=f"A{i}", ui_button=f"A{i}", target="a", trigger=trigger)
        for i, trigger in enumerate(triggers)
    ]
    return Automaton(
        init_action=init_action,
        states={
            "": State(key="", ui_label="", final=False, actions=[init_action]),
            "a": State(key="a", ui_label="a", final=not actions, contextual_prompt="hi", actions=actions),
        },
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


@pytest.mark.parametrize(("triggers", "names", "expected"), [
    (("engagement >= 50",), {"engagement"}, True),
    (("x >= 1", "engagement >= 50"), {"engagement"}, True),
    (("retention >= 50",), {"engagement", "retention", "state_stability"}, True),
    (("mySignal >= 50",), {"engagement"}, False),
    ((None,), {"engagement"}, False),
    ((), {"engagement"}, False),
], ids=["single-match", "one-of-several-actions", "any-name-of-the-set", "no-match", "manual-only", "final-state"])
def test_triggers_reference_is_true_iff_some_action_of_the_state_names_one_of_them(triggers, names, expected):
    assert _automaton(*triggers).triggers_reference("a", names) is expected
