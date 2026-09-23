"""The project-level `sources:` section (AutomatonBuilder._build_source)
— parsing each entry's ui-label/ui-description/url, validating the url's
scheme and (for the 'avance' driver, provisioning an empty archive when
its path hasn't been seen before rather than rejecting it) and validating
`source.<name>.<method>` references in trigger/env: expressions against
what's actually declared.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

_FLIGHTS = {"flights.csv": "a,b\n1,2\n"}
_PINO_FLIGHTS = "sources:\n  pino:\n    url: avance:flights.csv\n"


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
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: advance
        ui-label: Advance
        target: a
{trigger_line}
"""
    return AutomatonBuilder().build({"index.yml": content, **(contents or {})})


def test_a_declared_source_is_parsed_with_ui_label_defaulting_to_its_name_and_an_absent_section_or_url_still_builds():
    """A freshly-added source (AutomatonYamlEditor.add_source) has no url
    until the user configures one — buildable either way, exactly like an
    env key's own empty default."""
    full = _build(
        "sources:\n  pino:\n    ui-label: Flights\n    ui-description: Flight records.\n    url: avance:behaviour/flights.csv\n",
        contents={"behaviour/flights.csv": "a,b\n1,2\n"},
    ).sources[0]
    assert full.name == "pino"
    assert full.ui_label == "Flights"
    assert full.ui_description == "Flight records."
    assert full.url == "avance:behaviour/flights.csv"

    assert _build(_PINO_FLIGHTS, contents=_FLIGHTS).sources[0].ui_label == "pino"
    assert _build("").sources == []
    assert _build("sources:\n  pino:\n    ui-label: Flights\n").sources[0].url == ""


@pytest.mark.parametrize(("sources_yaml", "trigger", "match"), [
    ("sources:\n  - not\n  - a\n  - mapping\n", None, "'sources' must be a mapping"),
    ("sources:\n  pino:\n    url: flights.csv\n", None, "not a valid source url"),
    ("sources:\n  pino:\n    url: s3:flights.csv\n", None, "url scheme 's3' must be one of"),
    ("sources:\n  pino:\n    ui-label: Flights\n", "source.pino.select_rows_containing('x') != 'x'", r"undefined name\(s\).*source.pino.select"),
    ("sources:\n  pino:\n    url: websearch:flights.csv\n", None, "must be 'websearch:user'"),
], ids=["not-a-mapping", "no-scheme", "unknown-scheme", "referenced-without-url", "websearch-path-is-not-a-scope"])
def test_build_rejects_a_malformed_sources_section_a_bad_url_or_a_reference_to_a_source_with_no_url_yet(sources_yaml, trigger, match):
    with pytest.raises(ValueError, match=match):
        _build(sources_yaml, trigger=trigger)


@pytest.mark.parametrize(("trigger", "match"), [
    ("source.pino.row_where('caso' '=', 1) == {}", r"'caso' '=' is two string literals with no comma between them"),
    ("source.pino.row_where('caso', '=') == {}", r"row_where\(\.\.\.\) missing a required argument: 'value' — expected source.pino.row_where\(column, operator, value, \*strings\)"),
    ("source.pino.value('x') == ''", r"value\(\.\.\.\) missing a required argument: 'key'"),
    ("source.pino.value('x', key='a', extra=1) == ''", r"value\(\.\.\.\) got an unexpected keyword argument 'extra'"),
    ("source.pino.column() == []", r"column\(\.\.\.\) missing a required argument: 'column'"),
], ids=["merged-literals", "missing-positional", "missing-keyword", "unexpected-keyword", "no-arguments"])
def test_build_rejects_a_source_call_whose_arguments_do_not_fit_the_drivers_signature_and_says_which(trigger, match):
    with pytest.raises(ValueError, match=match):
        _build(_PINO_FLIGHTS, trigger=trigger, contents=_FLIGHTS)


def test_a_source_call_with_a_full_argument_list_or_extra_strings_builds():
    assert _build(_PINO_FLIGHTS, trigger="source.pino.row_where('a', '=', 1, 'x') == {}", contents=_FLIGHTS)
    assert _build(_PINO_FLIGHTS, trigger="source.pino.value('x', 'y', key='a') == ''", contents=_FLIGHTS)
    assert _build(_PINO_FLIGHTS, trigger="source.pino.select_rows_containing() == ''", contents=_FLIGHTS)


def test_an_avance_url_is_provisioned_empty_when_never_seen_and_a_trigger_may_call_a_declared_sources_select():
    """'avance' is the project's own embedded default driver — a source
    naming an archive it hasn't seen yet still builds, backed by an
    empty archive, instead of failing the whole project."""
    provisioned = _build(_PINO_FLIGHTS, trigger="source.pino.select_rows_containing('x') == 'x'")
    assert provisioned.sources[0].url == "avance:flights.csv"

    seeded = _build(_PINO_FLIGHTS, trigger="source.pino.select_rows_containing('x') != 'nope'", contents=_FLIGHTS)
    assert seeded.sources[0].name == "pino"


def test_a_websearch_url_builds_on_the_user_scope_alone_and_supports_the_same_reads_as_an_archive():
    built = _build(
        "sources:\n  web:\n    url: websearch:user\n",
        trigger="source.web.select_rows_containing('x') == 'x'",
    )
    assert built.sources[0].url == "websearch:user"


@pytest.mark.parametrize(("sources_yaml", "trigger", "match"), [
    ("", "source.pino.select_rows_containing('x') == 'x'", r"undefined name\(s\).*source.pino"),
    (_PINO_FLIGHTS, "source.pino.create('k', 'v') == None", r"undefined name\(s\).*source.pino.create"),
    (_PINO_FLIGHTS, "source.pino.read() == ''", r"attachment.<name>.read\(\)'s job"),
], ids=["undeclared-source", "unsupported-method", "read-points-to-attachment-read"])
def test_a_trigger_may_only_call_a_supported_method_on_a_declared_source(sources_yaml, trigger, match):
    with pytest.raises(ValueError, match=match):
        _build(sources_yaml, trigger=trigger, contents=_FLIGHTS)
