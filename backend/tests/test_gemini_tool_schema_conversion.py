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

from ai._providers.gemini_provider_v2 import GeminiProvider
from ai.llm_provider import AIServiceConfig, ToolSpec
from tracking.sources import METHOD_SCHEMAS

pytestmark = pytest.mark.contract


def _declaration(parameters: dict):
    provider = GeminiProvider(AIServiceConfig("gemini", "gemini-x", "k", None, "x"))
    spec = ToolSpec(name="source_env_update", description="d", parameters=parameters)
    declarations = provider._GeminiProvider__tool_declarations([spec], {"text": "the reply"})  # type: ignore[attr-defined]
    return declarations[1]


def test_the_uniform_select_schema_becomes_an_object_with_two_string_arrays():
    declaration = _declaration(METHOD_SCHEMAS["select"])

    properties = declaration.parameters.properties
    assert declaration.parameters.type == "OBJECT"
    assert properties["values"].type == "ARRAY" and properties["values"].items.type == "STRING"
    assert properties["keys"].type == "ARRAY" and properties["keys"].items.type == "STRING"
    assert declaration.parameters.required == ["values"]


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
