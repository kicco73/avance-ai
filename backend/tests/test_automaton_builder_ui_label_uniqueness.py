"""ui-label uniqueness (see automaton_builder.py's AutomatonBuilder.build/
_build_state) — enforced per scope: states and signals project-wide,
actions within their own containing state only (the same ui-label is
fine on actions in two different states).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(body: str):
    return AutomatonBuilder().build({"index.yml": f"project:\n  id: proj\ninit-action:\n  target: a\n{body}"})


@pytest.mark.parametrize(("body", "match"), [
    (
        "signals:\n  foo:\n    ui-label: Risk\n    definition: how risky\n  bar:\n    ui-label: Risk\n    definition: also risky\nstates:\n  a:\n    contextual-prompt: hi\n",
        r"Signals 'foo' and 'bar' both use ui-label 'Risk'",
    ),
    (
        "signals:\n  Risk:\n    definition: how risky\n  bar:\n    ui-label: Risk\n    definition: also risky\nstates:\n  a:\n    contextual-prompt: hi\n",
        r"ui-label 'Risk'",
    ),
    (
        "states:\n  a:\n    ui-label: Same\n    contextual-prompt: hi\n  b:\n    ui-label: Same\n    contextual-prompt: there\n",
        r"States 'a' and 'b' both use ui-label 'Same'",
    ),
    (
        "states:\n  a:\n    contextual-prompt: hi\n  b:\n    ui-label: a\n    contextual-prompt: there\n",
        r"ui-label 'a'",
    ),
    (
        "states:\n  a:\n    contextual-prompt: hi\n    actions:\n      - name: go-quiet\n        ui-label: Advance\n        target: b\n      - name: go-loud\n        ui-label: Advance\n        target: b\n  b:\n    contextual-prompt: there\n",
        r"State 'a': actions 'go-quiet' and 'go-loud' both use ui-label 'Advance'",
    ),
], ids=["signals", "signal-name-fallback", "states", "state-key-fallback", "actions-in-one-state"])
def test_a_duplicate_ui_label_within_one_scope_is_rejected_fallbacks_included(body, match):
    """ui-label absent falls back to the signal's own name / the state's
    own key — a fallback colliding with an explicit ui-label elsewhere is
    still a collision."""
    with pytest.raises(ValueError, match=match):
        _build(body)


def test_unique_ui_labels_build_fine_and_actions_in_different_states_may_share_one():
    automaton = _build("""signals:
  foo:
    ui-label: Foo signal
    definition: foo definition
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-a
        ui-label: Advance
        target: b
  b:
    ui-label: State B
    contextual-prompt: there
    actions:
      - name: go-b
        ui-label: Advance
        target: b
""")

    assert automaton.states["a"].ui_label == "State A"
    assert automaton.states["b"].ui_label == "State B"
    assert automaton.states["a"].actions[0].ui_label == "Advance"
    assert automaton.states["b"].actions[0].ui_label == "Advance"
