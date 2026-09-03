"""ui-label uniqueness (see automaton_builder.py's AutomatonBuilder.build/
_build_state) — enforced per scope: states and signals project-wide,
actions within their own containing state only (the same ui-label is
fine on actions in two different states).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def test_duplicate_signal_ui_label_is_rejected():
    content = """
project:
  id: proj
init-action:
  target: a
signals:
  foo:
    ui-label: Risk
    definition: how risky
  bar:
    ui-label: Risk
    definition: also risky
states:
  a:
    contextual-prompt: hi
"""
    with pytest.raises(ValueError, match=r"Signals 'foo' and 'bar' both use ui-label 'Risk'"):
        AutomatonBuilder().build({"index.yml": content})


def test_duplicate_signal_ui_label_via_the_name_fallback_is_rejected():
    # ui-label absent falls back to the signal's own name — a fallback
    # colliding with an explicit ui-label elsewhere is still a collision.
    content = """
project:
  id: proj
init-action:
  target: a
signals:
  Risk:
    definition: how risky
  bar:
    ui-label: Risk
    definition: also risky
states:
  a:
    contextual-prompt: hi
"""
    with pytest.raises(ValueError, match=r"ui-label 'Risk'"):
        AutomatonBuilder().build({"index.yml": content})


def test_duplicate_state_ui_label_is_rejected():
    content = """
project:
  id: proj
init-action:
  target: a
states:
  a:
    ui-label: Same
    contextual-prompt: hi
  b:
    ui-label: Same
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match=r"States 'a' and 'b' both use ui-label 'Same'"):
        AutomatonBuilder().build({"index.yml": content})


def test_duplicate_state_ui_label_via_the_key_fallback_is_rejected():
    # ui-label absent falls back to the state's own key (see
    # _build_state's raw_state.get("ui-label", key)).
    content = """
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
  b:
    ui-label: a
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match=r"ui-label 'a'"):
        AutomatonBuilder().build({"index.yml": content})


def test_duplicate_action_ui_label_within_the_same_state_is_rejected():
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
        ui-label: Advance
        target: b
      - name: go-loud
        ui-label: Advance
        target: b
  b:
    contextual-prompt: there
"""
    with pytest.raises(
        ValueError, match=r"State 'a': actions 'go-quiet' and 'go-loud' both use ui-label 'Advance'"
    ):
        AutomatonBuilder().build({"index.yml": content})


def test_same_action_ui_label_is_allowed_across_different_states():
    content = """
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go-a
        ui-label: Advance
        target: b
  b:
    contextual-prompt: there
    actions:
      - name: go-b
        ui-label: Advance
        target: b
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].ui_label == "Advance"
    assert automaton.states["b"].actions[0].ui_label == "Advance"


def test_unique_ui_labels_everywhere_build_without_error():
    content = """
project:
  id: proj
init-action:
  target: a
signals:
  foo:
    ui-label: Foo signal
    definition: foo definition
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go
        ui-label: Advance
        target: b
  b:
    ui-label: State B
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].ui_label == "State A"
    assert automaton.states["b"].ui_label == "State B"
