"""A state's own `ai-may-query-sources:`/`ai-must-query-sources:` fields
(State.ai_may_query_sources/ai_must_query_sources) — the subset of the
project's declared `sources:` this state exposes to the model as native
tool-calling targets (see tracking.sources.ToolSet). Validated at build
time against `sources:`, the same way action.target is validated against
declared states — plus each named source's own required `ai-definition`.
The removed `tools:` field (see project.archive.legacy_tools_field_migration
for the boot-time migration off it) is rejected outright here.
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


_FLIGHTS_SOURCE = "sources:\n  flights:\n    ui-label: Flights\n    ai-definition: One row per flight.\n"


def test_no_fields_leave_both_lists_empty():
    automaton = _build("")
    assert automaton.states["a"].ai_may_query_sources == ()
    assert automaton.states["a"].ai_must_query_sources == ()


def test_a_declared_ai_may_query_sources_list_is_parsed():
    automaton = _build("    ai-may-query-sources: [flights]", _FLIGHTS_SOURCE)
    assert automaton.states["a"].ai_may_query_sources == ("flights",)
    assert automaton.states["a"].ai_must_query_sources == ()


def test_a_declared_ai_must_query_sources_list_is_parsed():
    automaton = _build("    ai-must-query-sources: [flights]", _FLIGHTS_SOURCE)
    assert automaton.states["a"].ai_must_query_sources == ("flights",)
    assert automaton.states["a"].ai_may_query_sources == ()


def test_the_removed_tools_field_is_rejected():
    with pytest.raises(ValueError, match="'tools' is no longer a valid field"):
        _build("    tools: [flights]", _FLIGHTS_SOURCE)


def test_ai_may_query_sources_referencing_an_undeclared_source_is_rejected():
    with pytest.raises(ValueError, match="ai-may-query-sources 'flights'.*not declared"):
        _build("    ai-may-query-sources: [flights]")


def test_ai_must_query_sources_referencing_an_undeclared_source_is_rejected():
    with pytest.raises(ValueError, match="ai-must-query-sources 'flights'.*not declared"):
        _build("    ai-must-query-sources: [flights]")


def test_ai_may_query_sources_must_be_a_list_of_strings():
    with pytest.raises(ValueError, match="'ai-may-query-sources' must be a list of source names"):
        _build("    ai-may-query-sources: flights", _FLIGHTS_SOURCE)


def test_ai_must_query_sources_must_be_a_list_of_strings():
    with pytest.raises(ValueError, match="'ai-must-query-sources' must be a list of source names"):
        _build("    ai-must-query-sources: flights", _FLIGHTS_SOURCE)


def test_the_same_source_in_both_fields_for_one_state_is_rejected():
    with pytest.raises(ValueError, match="flights.*declared in both"):
        _build(
            "    ai-may-query-sources: [flights]\n    ai-must-query-sources: [flights]",
            _FLIGHTS_SOURCE,
        )


def test_a_source_with_no_ai_definition_is_rejected():
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build(
            "    ai-may-query-sources: [flights]",
            "sources:\n  flights:\n    url: avance:flights.csv\n",
            contents={"flights.csv": "a,b\n1,2\n"},
        )


def test_ai_definition_is_required_for_ai_must_query_sources_too():
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build(
            "    ai-must-query-sources: [flights]",
            "sources:\n  flights:\n    url: avance:flights.csv\n",
            contents={"flights.csv": "a,b\n1,2\n"},
        )


def test_a_source_with_no_url_yet_still_needs_an_ai_definition():
    # Same "created, not yet configured" leniency a bare source.<name>
    # reference gets for its own url — but ai-definition is still required
    # once the source is actually listed as a tool.
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build(
            "    ai-may-query-sources: [flights]",
            "sources:\n  flights:\n    ui-label: Flights\n",
        )


def test_a_source_with_no_url_yet_and_an_ai_definition_still_builds():
    automaton = _build(
        "    ai-may-query-sources: [flights]",
        "sources:\n  flights:\n    ui-label: Flights\n    ai-definition: One row per flight.\n",
    )
    assert automaton.states["a"].ai_may_query_sources == ("flights",)


def test_a_source_not_listed_anywhere_needs_no_ai_definition():
    automaton = _build("", _FLIGHTS_SOURCE.replace("ai-definition: One row per flight.\n", ""))
    assert automaton.sources[0].ai_definition is None
