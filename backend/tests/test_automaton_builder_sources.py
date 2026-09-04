"""The project-level `sources:` section (AutomatonBuilder._build_source)
— parsing each entry's ui-label/ui-description/url, validating the url's
scheme and (for the 'avance' driver) that its path resolves to an
already-uploaded archive, and validating `source.<name>.<method>`
references in trigger/env: expressions against what's actually declared.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(sources_yaml: str, trigger: str | None = None, contents: dict[str, str] | None = None) -> object:
    trigger_line = f'        trigger: "{trigger}"' if trigger else ""
    content = f"""
project:
  id: test_project
{sources_yaml}
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: advance
        ui-label: Advance
        target: a
{trigger_line}
"""
    return AutomatonBuilder().build({"index.yml": content, **(contents or {})})


def test_a_declared_source_is_parsed():
    automaton = _build(
        """
sources:
  pino:
    ui-label: Flights
    ui-description: Flight records.
    url: avance:behaviour/flights.csv
""",
        contents={"behaviour/flights.csv": "a,b\n1,2\n"},
    )
    source = automaton.sources[0]
    assert source.name == "pino"
    assert source.ui_label == "Flights"
    assert source.ui_description == "Flight records."
    assert source.url == "avance:behaviour/flights.csv"


def test_ui_label_defaults_to_the_source_name():
    automaton = _build(
        "sources:\n  pino:\n    url: avance:flights.csv\n",
        contents={"flights.csv": "a,b\n1,2\n"},
    )
    assert automaton.sources[0].ui_label == "pino"


def test_no_sources_section_leaves_sources_empty():
    automaton = _build("")
    assert automaton.sources == []


def test_sources_section_must_be_a_mapping():
    with pytest.raises(ValueError, match="'sources' must be a mapping"):
        _build("sources:\n  - not\n  - a\n  - mapping\n")


def test_a_source_with_no_url_yet_builds_fine_but_can_t_be_referenced():
    """A freshly-added source (AutomatonYamlEditor.add_source) has no
    url until the user configures one — buildable either way, exactly
    like an env key's own empty default."""
    automaton = _build("sources:\n  pino:\n    ui-label: Flights\n")
    assert automaton.sources[0].url == ""

    with pytest.raises(ValueError, match="undefined name\\(s\\).*source.pino.select"):
        _build("sources:\n  pino:\n    ui-label: Flights\n", trigger="source.pino.select('x') != 'x'")


def test_url_without_a_scheme_is_rejected():
    with pytest.raises(ValueError, match="not a valid source url"):
        _build("sources:\n  pino:\n    url: flights.csv\n")


def test_url_with_an_unknown_scheme_is_rejected():
    with pytest.raises(ValueError, match="url scheme 's3' must be one of"):
        _build("sources:\n  pino:\n    url: s3:flights.csv\n")


def test_avance_url_referencing_a_missing_archive_is_rejected():
    with pytest.raises(ValueError, match="not found"):
        _build("sources:\n  pino:\n    url: avance:flights.csv\n")


def test_a_trigger_may_call_a_declared_source_s_select():
    automaton = _build(
        """
sources:
  pino:
    url: avance:flights.csv
""",
        trigger="source.pino.select('x') != 'nope'",
        contents={"flights.csv": "a,b\n1,2\n"},
    )
    assert automaton.sources[0].name == "pino"


def test_a_trigger_referencing_an_undeclared_source_is_rejected():
    with pytest.raises(ValueError, match="undefined name\\(s\\).*source.pino"):
        _build("", trigger="source.pino.select('x') == 'x'")


def test_a_trigger_calling_an_unsupported_method_on_a_declared_source_is_rejected():
    with pytest.raises(ValueError, match="undefined name\\(s\\).*source.pino.create"):
        _build(
            "sources:\n  pino:\n    url: avance:flights.csv\n",
            trigger="source.pino.create('k', 'v') == None",
            contents={"flights.csv": "a,b\n1,2\n"},
        )
