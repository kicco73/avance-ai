"""A state's own `ai-may-read-sources:`/`ai-must-read-sources:`/
`ai-may-write-sources:` fields (State.ai_may_read_sources/
ai_must_read_sources/ai_may_write_sources) — the subset of the project's
declared `sources:` this state exposes to the model as native tool-calling
targets (see tracking.sources.ToolSet): `select` for a read field, `update`
for the write one. Validated at build time against `sources:`, the same
way action.target is validated against declared states — plus each named
source's own required `ai-definition`, and (for a write) its driver's own
update support. The legacy names (`tools`, `ai-may-query-sources`,
`ai-must-query-sources` — see project.archive.legacy_tools_field_migration
for the boot-time migration off them) are rejected outright here.
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

_ENV_PROJECT = """
env:
  pnr:
    ai-access: readwrite
    ai-definition: The booking's own record locator.
  customer_email:
    ai-access: readonly
    ai-definition: The customer's email.
    value: user.email
  _hidden:
    value: "'x'"
sources:
  env:
    url: avance:env
    ui-label: Env
    ai-definition: The automaton's variables.
  flights:
    ui-label: Flights
    ai-definition: One row per flight.
"""


def test_no_fields_leave_every_list_empty():
    automaton = _build("")
    state = automaton.states["a"]
    assert state.ai_may_read_sources == ()
    assert state.ai_must_read_sources == ()
    assert state.ai_may_write_sources == ()
    assert state.ai_source_names == ()


def test_a_declared_ai_may_read_sources_list_is_parsed():
    automaton = _build("    ai-may-read-sources: [flights]", _FLIGHTS_SOURCE)
    assert automaton.states["a"].ai_may_read_sources == ("flights",)
    assert automaton.states["a"].ai_must_read_sources == ()


def test_a_declared_ai_must_read_sources_list_is_parsed():
    automaton = _build("    ai-must-read-sources: [flights]", _FLIGHTS_SOURCE)
    assert automaton.states["a"].ai_must_read_sources == ("flights",)
    assert automaton.states["a"].ai_may_read_sources == ()


def test_a_declared_ai_may_write_sources_list_is_parsed_for_a_source_whose_driver_supports_update():
    automaton = _build("    ai-may-read-sources: [env]\n    ai-may-write-sources: [env]", _ENV_PROJECT)
    state = automaton.states["a"]
    assert state.ai_may_write_sources == ("env",)
    assert state.ai_source_names == ("env", "env")
    assert automaton.build_warnings == []


@pytest.mark.parametrize("legacy_field, replacement", [
    ("tools", "ai-may-read-sources"),
    ("ai-may-query-sources", "ai-may-read-sources"),
    ("ai-must-query-sources", "ai-must-read-sources"),
])
def test_a_legacy_field_name_is_rejected_naming_its_replacement(legacy_field, replacement):
    with pytest.raises(ValueError, match=rf"'{legacy_field}' is no longer a valid field — use '{replacement}'"):
        _build(f"    {legacy_field}: [flights]", _FLIGHTS_SOURCE)


@pytest.mark.parametrize("field_name", ["ai-may-read-sources", "ai-must-read-sources", "ai-may-write-sources"])
def test_referencing_an_undeclared_source_is_rejected(field_name):
    with pytest.raises(ValueError, match=rf"{field_name} 'flights'.*not declared"):
        _build(f"    {field_name}: [flights]")


@pytest.mark.parametrize("field_name", ["ai-may-read-sources", "ai-must-read-sources", "ai-may-write-sources"])
def test_each_field_must_be_a_list_of_strings(field_name):
    with pytest.raises(ValueError, match=rf"'{field_name}' must be a list of source names"):
        _build(f"    {field_name}: flights", _FLIGHTS_SOURCE)


def test_the_same_source_in_both_read_fields_for_one_state_is_rejected():
    with pytest.raises(ValueError, match="flights.*declared in both 'ai-may-read-sources' and 'ai-must-read-sources'"):
        _build("    ai-may-read-sources: [flights]\n    ai-must-read-sources: [flights]", _FLIGHTS_SOURCE)


def test_a_write_on_a_source_whose_driver_has_no_update_is_rejected_like_an_unsupported_script_method():
    with pytest.raises(ValueError, match=r"ai-may-write-sources 'flights' references undefined name\(s\): source.flights.update"):
        _build(
            "    ai-may-write-sources: [flights]",
            "sources:\n  flights:\n    url: avance:flights.csv\n    ai-definition: One row per flight.\n",
            contents={"flights.csv": "a,b\n1,2\n"},
        )


def test_a_write_on_an_env_source_the_state_never_reads_is_a_warning_not_an_error():
    automaton = _build("    ai-may-write-sources: [env]", _ENV_PROJECT)
    assert automaton.states["a"].ai_may_write_sources == ("env",)
    assert len(automaton.build_warnings) == 1
    assert "ai-may-write-sources 'env'" in automaton.build_warnings[0]
    assert "ai-may-read-sources" in automaton.build_warnings[0]


def test_a_write_on_an_env_source_the_state_must_read_raises_no_warning():
    automaton = _build("    ai-must-read-sources: [env]\n    ai-may-write-sources: [env]", _ENV_PROJECT)
    assert automaton.build_warnings == []


def test_a_source_with_no_ai_definition_is_rejected():
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build(
            "    ai-may-read-sources: [flights]",
            "sources:\n  flights:\n    url: avance:flights.csv\n",
            contents={"flights.csv": "a,b\n1,2\n"},
        )


def test_ai_definition_is_required_for_ai_must_read_sources_too():
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build(
            "    ai-must-read-sources: [flights]",
            "sources:\n  flights:\n    url: avance:flights.csv\n",
            contents={"flights.csv": "a,b\n1,2\n"},
        )


def test_a_source_with_no_url_yet_still_needs_an_ai_definition():
    # Same "created, not yet configured" leniency a bare source.<name>
    # reference gets for its own url — but ai-definition is still required
    # once the source is actually listed as a tool.
    with pytest.raises(ValueError, match="flights.*has no own 'ai-definition'"):
        _build("    ai-may-read-sources: [flights]", "sources:\n  flights:\n    ui-label: Flights\n")


def test_a_source_with_no_url_yet_and_an_ai_definition_still_builds_as_a_read_target():
    # "Created, not yet configured" leniency — a read on a url-less source
    # builds; it just can't be called until a url picks a driver.
    automaton = _build("    ai-may-read-sources: [flights]", _FLIGHTS_SOURCE)
    assert automaton.states["a"].ai_may_read_sources == ("flights",)


def test_a_write_on_a_source_with_no_url_yet_is_rejected():
    # No url means no driver, so no update to expose — unlike a read,
    # there is nothing lenient about promising a write nobody can serve.
    with pytest.raises(ValueError, match=r"undefined name\(s\): source.flights.update"):
        _build("    ai-may-write-sources: [flights]", _FLIGHTS_SOURCE)


def test_a_source_not_listed_anywhere_needs_no_ai_definition():
    automaton = _build("", _FLIGHTS_SOURCE.replace("ai-definition: One row per flight.\n", ""))
    assert automaton.sources[0].ai_definition is None


class TestEnvSource:
    def test_an_env_source_with_no_exported_key_is_rejected(self):
        with pytest.raises(ValueError, match="avance:env.*no env key declares 'ai-access: readonly' or 'ai-access: readwrite'"):
            _build("", """
env:
  _hidden:
    value: "'x'"
sources:
  env:
    url: avance:env
    ai-definition: The automaton's variables.
""")

    def test_exported_keys_with_no_env_source_build_fine(self):
        automaton = _build("", """
env:
  pnr:
    ai-access: readwrite
    ai-definition: The record locator.
""")
        assert [env_key.name for env_key in automaton.exported_env_keys()] == ["pnr"]
        assert automaton.sources == []

    def test_an_env_source_never_provisions_an_archive_named_env(self):
        automaton = _build("", _ENV_PROJECT)
        assert "env" not in automaton.attachments
        assert automaton.sources[0].is_env_source

    def test_reads_env_source_is_true_only_for_a_state_listing_it_in_a_read_field(self):
        reading = _build("    ai-may-read-sources: [env]", _ENV_PROJECT)
        assert reading.reads_env_source(reading.states["a"]) is True
        writing_only = _build("    ai-may-write-sources: [env]", _ENV_PROJECT)
        assert writing_only.reads_env_source(writing_only.states["a"]) is False
        other_source = _build("    ai-may-read-sources: [flights]", _ENV_PROJECT)
        assert other_source.reads_env_source(other_source.states["a"]) is False

    def test_a_script_may_call_select_with_keys_and_update_with_fields_on_an_env_source(self):
        content = """
project:
  id: test_project
env:
  pnr:
    ai-access: readwrite
    ai-definition: The record locator.
sources:
  env:
    url: avance:env
    ai-definition: The variables.
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: advance
        target: a
        trigger: "source.env.select(keys=['pnr']) != ''"
        on-enter: |
          source.env.update(fields={'pnr': 'X'})
"""
        automaton = AutomatonBuilder().build({"index.yml": content})
        assert automaton.states["a"].actions[0].trigger == "source.env.select(keys=['pnr']) != ''"

    def test_a_script_calling_update_on_an_archive_source_is_rejected(self):
        content = """
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
        on-enter: |
          source.flights.update(fields={'a': 'b'})
"""
        with pytest.raises(ValueError, match=r"undefined name\(s\): source.flights.update"):
            AutomatonBuilder().build({"index.yml": content, "flights.csv": "a,b\n1,2\n"})
