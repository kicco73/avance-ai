"""The `reactions:` section and per-state `reactions-enabled:` flag — see
automaton_builder.py's AutomatonBuilder._build_reaction/_build_state and
automaton.py's Reaction/State.reactions_enabled.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def test_reaction_fields_parse_with_ui_description_fallback_to_definition():
    content = """
project:
  id: proj
init-action:
  target: a
reactions:
  supportive:
    ui-label: "🙏"
    ui-description: Silent acknowledgment.
    definition: Use when a verbal response would feel clinical.
  neutral:
    ui-label: "👍"
    definition: A plain acknowledgment, no further nuance.
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    by_name = {r.name: r for r in automaton.reactions}

    assert by_name["supportive"].ui_label == "🙏"
    assert by_name["supportive"].ui_description == "Silent acknowledgment."
    assert by_name["supportive"].definition == "Use when a verbal response would feel clinical."

    # No ui-description given -> falls back to definition, same as Signal.
    assert by_name["neutral"].ui_description == by_name["neutral"].definition


def test_reaction_ui_label_falls_back_to_the_reaction_name():
    content = """
project:
  id: proj
init-action:
  target: a
reactions:
  supportive:
    definition: Use when a verbal response would feel clinical.
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.reactions[0].ui_label == "supportive"


def test_no_reactions_section_leaves_an_empty_list():
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
    assert automaton.reactions == []


def test_duplicate_reaction_ui_label_is_rejected():
    content = """
project:
  id: proj
init-action:
  target: a
reactions:
  foo:
    ui-label: Same
    definition: foo definition
  bar:
    ui-label: Same
    definition: bar definition
states:
  a:
    contextual-prompt: hi
"""
    with pytest.raises(ValueError, match=r"Reactions 'foo' and 'bar' both use ui-label 'Same'"):
        AutomatonBuilder().build({"index.yml": content})


def test_reactions_must_be_a_mapping():
    content = """
project:
  id: proj
init-action:
  target: a
reactions:
  - not a mapping
states:
  a:
    contextual-prompt: hi
"""
    with pytest.raises(ValueError, match=r"'reactions' must be a mapping"):
        AutomatonBuilder().build({"index.yml": content})


def test_reactions_enabled_defaults_to_false():
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
    assert automaton.states["a"].reactions_enabled is False


def test_reactions_enabled_parses_per_state():
    content = """
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    reactions-enabled: true
  b:
    contextual-prompt: there
    reactions-enabled: false
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].reactions_enabled is True
    assert automaton.states["b"].reactions_enabled is False


class TestReactionsEnabledFor:
    """Automaton.reactions_enabled_for — the effective, runtime "can the
    bot actually attach a reaction here" check TrackingProcessor's own
    build_turn_protocol/estimate_state_prompt both use, as opposed to a
    state's raw, unguarded reactions_enabled flag above."""

    def test_true_when_the_state_opts_in_and_the_project_declares_reactions(self):
        automaton = AutomatonBuilder().build({"index.yml": """
project:
  id: proj
init-action:
  target: a
reactions:
  supportive:
    definition: Use when a verbal response would feel clinical.
states:
  a:
    contextual-prompt: hi
    reactions-enabled: true
"""})
        assert automaton.reactions_enabled_for(automaton.states["a"]) is True

    def test_false_when_the_state_opts_in_but_the_project_declares_no_reactions(self):
        automaton = AutomatonBuilder().build({"index.yml": """
project:
  id: proj
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    reactions-enabled: true
"""})
        assert automaton.reactions_enabled_for(automaton.states["a"]) is False

    def test_false_when_the_project_declares_reactions_but_the_state_opts_out(self):
        automaton = AutomatonBuilder().build({"index.yml": """
project:
  id: proj
init-action:
  target: a
reactions:
  supportive:
    definition: Use when a verbal response would feel clinical.
states:
  a:
    contextual-prompt: hi
    reactions-enabled: false
"""})
        assert automaton.reactions_enabled_for(automaton.states["a"]) is False
