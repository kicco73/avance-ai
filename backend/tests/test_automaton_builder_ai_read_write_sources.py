"""A state's own `ai-may-read-sources:`/`ai-must-read-sources:`/
`ai-may-write-sources:` fields (State.ai_may_read_sources/
ai_must_read_sources/ai_may_write_sources) — the subset of the project's
declared `sources:` this state exposes to the model as native tool-calling
targets (see tracking.sources.ToolSet): `select` for a read field, `update`
for the write one. Validated at build time against `sources:`, the same
way action.target is validated against declared states — plus each named
source's own required `ai-definition`, and (for a write) its driver's own
update support. The legacy names (`tools`, `ai-may-query-sources`,
`ai-must-query-sources`) are rejected outright here.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(state_yaml: str, top_yaml: str = "", contents: dict[str, str] | None = None) -> object:
    content = f"""
project:
  id: test_project
{top_yaml}
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
_FLIGHTS_CSV_NO_DEFINITION = "sources:\n  flights:\n    url: avance:flights.csv\n"
_FLIGHTS_CSV = {"flights.csv": "a,b\n1,2\n"}


def test_each_field_is_parsed_into_its_own_tuple_leaving_the_others_empty():
    bare = _build("").states["a"]
    assert bare.ai_may_read_sources == ()
    assert bare.ai_must_read_sources == ()
    assert bare.ai_may_write_sources == ()
    assert bare.ai_source_names == ()

    may = _build("    ai-may-read-sources: [flights]", _FLIGHTS_SOURCE).states["a"]
    assert may.ai_may_read_sources == ("flights",)
    assert may.ai_must_read_sources == ()

    must = _build("    ai-must-read-sources: [flights]", _FLIGHTS_SOURCE).states["a"]
    assert must.ai_must_read_sources == ("flights",)
    assert must.ai_may_read_sources == ()


@pytest.mark.parametrize("legacy_field, replacement", [
    ("tools", "ai-may-read-sources"),
    ("ai-may-query-sources", "ai-may-read-sources"),
    ("ai-must-query-sources", "ai-must-read-sources"),
])
def test_a_legacy_field_name_is_rejected_naming_its_replacement(legacy_field, replacement):
    with pytest.raises(ValueError, match=rf"'{legacy_field}' is no longer a valid field — use '{replacement}'"):
        _build(f"    {legacy_field}: [flights]", _FLIGHTS_SOURCE)


@pytest.mark.parametrize("field_name", ["ai-may-read-sources", "ai-must-read-sources", "ai-may-write-sources"])
def test_each_field_must_be_a_list_of_declared_source_names(field_name):
    with pytest.raises(ValueError, match=rf"{field_name} 'flights'.*not declared"):
        _build(f"    {field_name}: [flights]")
    with pytest.raises(ValueError, match=rf"'{field_name}' must be a list of source names"):
        _build(f"    {field_name}: flights", _FLIGHTS_SOURCE)


def test_a_source_may_not_be_both_may_and_must_read_and_a_write_needs_a_driver_with_update():
    # No url means no driver, so no update to expose — unlike a read,
    # there is nothing lenient about promising a write nobody can serve.
    with pytest.raises(ValueError, match="flights.*declared in both 'ai-may-read-sources' and 'ai-must-read-sources'"):
        _build("    ai-may-read-sources: [flights]\n    ai-must-read-sources: [flights]", _FLIGHTS_SOURCE)
    with pytest.raises(ValueError, match=r"ai-may-write-sources 'flights' references undefined name\(s\): source.flights.update"):
        _build("    ai-may-write-sources: [flights]", _FLIGHTS_CSV_NO_DEFINITION + "    ai-definition: One row per flight.\n", contents=_FLIGHTS_CSV)
    with pytest.raises(ValueError, match=r"undefined name\(s\): source.flights.update"):
        _build("    ai-may-write-sources: [flights]", _FLIGHTS_SOURCE)


def test_ai_definition_is_required_only_once_a_source_is_listed_even_before_it_has_a_url():
    # "Created, not yet configured" leniency — a read on a url-less source
    # builds; it just can't be called until a url picks a driver. But
    # ai-definition is still required once the source is actually listed.
    for field in ("ai-may-read-sources", "ai-must-read-sources"):
        with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
            _build(f"    {field}: [flights]", _FLIGHTS_CSV_NO_DEFINITION, contents=_FLIGHTS_CSV)
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build("    ai-may-read-sources: [flights]", "sources:\n  flights:\n    ui-label: Flights\n")

    assert _build("    ai-may-read-sources: [flights]", _FLIGHTS_SOURCE).states["a"].ai_may_read_sources == ("flights",)
    assert _build("", _FLIGHTS_SOURCE.replace("ai-definition: One row per flight.\n", "")).sources[0].ai_definition is None


def test_a_script_may_never_update_a_source_whose_driver_has_no_write_support():
    archive_script = """
project:
  id: test_project
sources:
  flights:
    url: avance:flights.csv
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: advance
        target: a
        task: |
          source.flights.update(fields={'a': 'b'})
"""
    with pytest.raises(ValueError, match=r"undefined name\(s\): source.flights.update"):
        AutomatonBuilder().build({"index.yml": archive_script, **_FLIGHTS_CSV})
