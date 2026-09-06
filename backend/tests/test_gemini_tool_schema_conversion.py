"""GeminiProvider's translation of a ToolSpec's plain JSON Schema into
Gemini's own Schema dialect — the uniform method schemas
(tracking.sources.METHOD_SCHEMAS: arrays of strings, a string→string
object) and a driver's narrowing of them (enums, fixed object
properties, descriptions — see AvanceEnvSource.parameter_schema) must
all survive the trip; the keywords Gemini doesn't know
(additionalProperties/minProperties/minItems) are dropped, never sent.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from ai._providers.gemini_provider_v2 import GeminiProvider
from ai.llm_provider import AIServiceConfig, ToolSpec
from tracking.sources import METHOD_SCHEMAS

pytestmark = pytest.mark.contract


def _declaration(parameters: dict):
    provider = GeminiProvider(AIServiceConfig("gemini", "gemini-x", "k", None, "x"))
    spec = ToolSpec(name="source_env_update", description="d", parameters=parameters)
    declarations = provider._GeminiProvider__tool_declarations([spec], {"text": "the reply"})  # type: ignore[attr-defined]
    return declarations[1]


def test_the_uniform_read_schemas_become_objects_of_string_arrays_strings_and_enums():
    declaration = _declaration(METHOD_SCHEMAS["select_rows_containing"])

    properties = declaration.parameters.properties
    assert declaration.parameters.type == "OBJECT"
    assert properties["values"].type == "ARRAY" and properties["values"].items.type == "STRING"
    assert declaration.parameters.required == ["values"]

    column = _declaration(METHOD_SCHEMAS["select_rows_where"])
    assert column.parameters.properties["column"].type == "STRING"
    assert list(column.parameters.properties["operator"].enum) == ["=", "!=", ">", ">=", "<", "<="]
    assert column.parameters.required == ["column", "operator", "value"]
    assert column.parameters.properties["strings"].type == "ARRAY"

    ranged = _declaration(METHOD_SCHEMAS["select_rows_in_range"])
    assert ranged.parameters.properties["start"].type == "STRING"
    assert ranged.parameters.required == ["column", "start", "end"]
    assert ranged.parameters.properties["strings"].type == "ARRAY"


def test_a_narrowed_update_schema_keeps_enums_properties_and_descriptions_and_drops_the_unknown_keywords():
    declaration = _declaration({
        "type": "object",
        "properties": {
            "values": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
            "fields": {
                "type": "object",
                "properties": {"pnr": {"type": "string", "description": "The record locator."}},
                "additionalProperties": False, "minProperties": 1,
                "description": "Variable → value.",
            },
        },
        "required": ["values", "fields"],
    })

    fields = declaration.parameters.properties["fields"]
    assert fields.type == "OBJECT"
    assert fields.description == "Variable → value."
    assert fields.properties["pnr"].type == "STRING"
    assert fields.properties["pnr"].description == "The record locator."
    assert declaration.parameters.properties["values"].items.enum == ["a", "b"]
    assert declaration.parameters.required == ["values", "fields"]
    dumped = fields.model_dump(exclude_none=True)
    assert "additionalProperties" not in dumped and "minProperties" not in dumped


def test_an_update_tool_with_an_empty_fields_schema_can_never_reach_this_conversion_at_all():
    """An avance:env source's own `update` schema narrows `fields` to just
    its readwrite keys (AvanceEnvSource.parameter_schema) — if none exist,
    that would be an object schema with zero properties, and Gemini's own
    Schema (unlike plain JSON Schema) rejects an OBJECT with no properties
    outright. This never has to be handled here because AutomatonBuilder
    refuses to build a state's own ai-may-write-sources for an avance:env
    source with no 'ai-access: readwrite' key at all (see
    AutomatonBuilder._validate_state_sources) — so no ToolSet, and so no
    tool declaration, for a schema shaped like this ever exists to send to
    any provider, Gemini included."""
    with pytest.raises(ValueError, match="no env key declares 'ai-access: readwrite'"):
        AutomatonBuilder().build({"index.yml": """
project:
  id: test_project
env:
  customer_email:
    ai-access: readonly
    ai-definition: The customer's email.
sources:
  env:
    url: avance:env
    ai-definition: The automaton's variables.
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    ai-may-write-sources: [env]
    actions:
      - name: advance
        ui-label: Advance
        target: a
"""})
