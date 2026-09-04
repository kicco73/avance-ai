"""A state's own `tools:` field (State.tools) — the subset of the
project's declared `sources:` this state exposes to the model as native
tool-calling targets (see tracking.sources.ToolSet). Validated at build
time against `sources:`, the same way action.target is validated against
declared states.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(state_yaml: str, sources_yaml: str = "", contents: dict[str, str] | None = None) -> object:
    content = f"""
project:
  id: test_project
{sources_yaml}
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
{state_yaml}
    actions:
      - name: advance
        ui-label: Advance
        target: a
"""
    return AutomatonBuilder().build({"index.yml": content, **(contents or {})})


def test_no_tools_field_leaves_state_tools_empty():
    automaton = _build("")
    assert automaton.states["a"].tools == ()


def test_a_declared_tools_list_is_parsed():
    automaton = _build(
        "    tools: [flights]",
        "sources:\n  flights:\n    url: avance:flights.csv\n",
        contents={"flights.csv": "a,b\n1,2\n"},
    )
    assert automaton.states["a"].tools == ("flights",)


def test_tools_referencing_an_undeclared_source_is_rejected():
    with pytest.raises(ValueError, match="tools 'flights'.*not declared"):
        _build("    tools: [flights]")


def test_tools_must_be_a_list_of_strings():
    with pytest.raises(ValueError, match="'tools' must be a list of source names"):
        _build("    tools: flights")


def test_tools_referencing_a_declared_source_with_no_url_yet_still_builds():
    # Same "created, not yet configured" leniency a bare source.<name>
    # reference gets — the source exists, it just can't be usefully
    # called yet (ToolSet.specs() would find no driver for it).
    automaton = _build(
        "    tools: [flights]",
        "sources:\n  flights:\n    ui-label: Flights\n",
    )
    assert automaton.states["a"].tools == ("flights",)
