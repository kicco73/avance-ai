"""The `reactions:` section and per-state `reactions-enabled:` flag — see
automaton_builder.py's AutomatonBuilder._build_reaction/_build_state and
automaton.py's Reaction/State.reactions_enabled.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

SUPPORTIVE = "reactions:\n  supportive:\n    definition: Use when a verbal response would feel clinical.\n"


def _build(reactions_yaml: str = "", states_yaml: str = "  a:\n    contextual-prompt: hi\n"):
    content = f"""
project:
  id: proj
init-action:
  target: a
{reactions_yaml}states:
{states_yaml}"""
    return AutomatonBuilder().build({"index.yml": content})


def test_reaction_fields_parse_with_ui_description_falling_back_to_definition_and_ui_label_to_the_name():
    automaton = _build("""
reactions:
  supportive:
    ui-label: "🙏"
    ui-description: Silent acknowledgment.
    definition: Use when a verbal response would feel clinical.
  neutral:
    ui-label: "👍"
    definition: A plain acknowledgment, no further nuance.
""")
    by_name = {r.name: r for r in automaton.reactions}
    assert by_name["supportive"].ui_label == "🙏"
    assert by_name["supportive"].ui_description == "Silent acknowledgment."
    assert by_name["supportive"].definition == "Use when a verbal response would feel clinical."
    assert by_name["neutral"].ui_description == by_name["neutral"].definition

    assert _build(SUPPORTIVE).reactions[0].ui_label == "supportive"
    assert _build().reactions == []


def test_reactions_must_be_a_mapping_with_unique_ui_labels():
    with pytest.raises(ValueError, match=r"'reactions' must be a mapping"):
        _build("reactions:\n  - not a mapping\n")
    with pytest.raises(ValueError, match=r"Reactions 'foo' and 'bar' both use ui-label 'Same'"):
        _build("reactions:\n  foo:\n    ui-label: Same\n    definition: foo definition\n  bar:\n    ui-label: Same\n    definition: bar definition\n")


def test_reactions_are_effectively_enabled_only_where_a_state_opts_in_and_the_project_declares_some():
    """Automaton.reactions_enabled_for — the effective, runtime "can the
    bot actually attach a reaction here" check TrackingProcessor's own
    build_turn_protocol/estimate_state_prompt both use, as opposed to a
    state's raw, unguarded reactions_enabled flag."""
    assert _build().states["a"].reactions_enabled is False

    per_state = _build(states_yaml="  a:\n    contextual-prompt: hi\n    reactions-enabled: true\n  b:\n    contextual-prompt: there\n    reactions-enabled: false\n")
    assert per_state.states["a"].reactions_enabled is True
    assert per_state.states["b"].reactions_enabled is False
    assert per_state.reactions_enabled_for(per_state.states["a"]) is False

    declared = _build(SUPPORTIVE, states_yaml="  a:\n    contextual-prompt: hi\n    reactions-enabled: true\n  b:\n    contextual-prompt: there\n    reactions-enabled: false\n")
    assert declared.reactions_enabled_for(declared.states["a"]) is True
    assert declared.reactions_enabled_for(declared.states["b"]) is False
