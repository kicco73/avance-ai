from __future__ import annotations

import pytest

from ai.llm_provider import AIServiceConfig
from ai.response_schema import BooleanField, NumberField, ObjectField, StringField
from ai._providers.anthropic_provider_v2 import AnthropicProvider
from ai._providers.gemini_provider_v2 import GeminiProvider
from ai._providers.openai_provider_v2 import OpenAICompatibleProvider

pytestmark = pytest.mark.contract

SCHEMA = {
    "text": StringField("The reply."),
    "output": ObjectField({
        "sugerencias": StringField().nullable(),
        "conversacion_terminada": BooleanField().nullable(),
    }, "Declared outputs."),
    "signals": ObjectField({"empatia": NumberField()}),
}


def _config(driver: str) -> AIServiceConfig:
    return AIServiceConfig(driver=driver, model="model", key="key", url=None, ui_label=driver)


@pytest.mark.parametrize("provider_class", [OpenAICompatibleProvider, AnthropicProvider])
def test_a_json_schema_provider_is_asked_for_real_nested_objects_with_nullable_fields(provider_class):
    schema = provider_class(_config("openai")).build_schema(SCHEMA)

    assert schema == {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The reply."},
            "output": {
                "type": "object",
                "description": "Declared outputs.",
                "properties": {
                    "sugerencias": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "conversacion_terminada": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                },
                "required": ["sugerencias", "conversacion_terminada"],
                "additionalProperties": False,
            },
            "signals": {
                "type": "object",
                "properties": {"empatia": {"type": "number"}},
                "required": ["empatia"],
                "additionalProperties": False,
            },
        },
        "required": ["text", "output", "signals"],
        "additionalProperties": False,
    }


def test_gemini_is_asked_for_the_same_objects_in_its_own_dialect():
    schema = GeminiProvider(_config("gemini")).build_schema(SCHEMA)

    assert schema == {
        "type": "OBJECT",
        "properties": {
            "text": {"type": "STRING", "description": "The reply."},
            "output": {
                "type": "OBJECT",
                "description": "Declared outputs.",
                "properties": {
                    "sugerencias": {"type": "STRING", "nullable": True},
                    "conversacion_terminada": {"type": "BOOLEAN", "nullable": True},
                },
                "required": ["sugerencias", "conversacion_terminada"],
            },
            "signals": {"type": "OBJECT", "properties": {"empatia": {"type": "NUMBER"}}, "required": ["empatia"]},
        },
        "required": ["text", "output", "signals"],
    }
