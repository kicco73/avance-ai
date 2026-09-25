from __future__ import annotations

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from ai.llm_provider import AIServiceProviderMalformedReplyError, AIServiceProviderPermanentError, AIServiceRequestError
from ai.response_schema import NumberField, ObjectField, StringField
from provider_tools_helpers import OpenAIChunk, OpenAIHarness, OpenAIUsage, _OpenAIChoice, _OpenAIDelta

harness = OpenAIHarness()

REQUEST = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")


@pytest.mark.asyncio
async def test_connection_refused_cascades_instead_of_getting_stuck() -> None:
    """A local llama.cpp/LM Studio server that isn't running raises
    openai.APIConnectionError — never an HTTP status, since the request
    never reached a server at all. This must map to
    AIServiceProviderPermanentError (a cascade failover trigger, see
    cascading_llm_provider.py's _FAILOVER_ERRORS), not the generic
    AIServiceError, or the cascade pointer never advances and every
    subsequent call keeps retrying the same dead provider forever."""
    connection_error = APIConnectionError(message="Connection refused", request=REQUEST)
    provider, _ = harness.provider([], errors=[connection_error])

    with pytest.raises(AIServiceProviderPermanentError):
        async for _ in provider.generate_stream_with_schema("system prompt", [], {"text": StringField("t")}):
            pass


@pytest.mark.asyncio
async def test_bad_request_maps_to_request_error_not_permanent() -> None:
    response = httpx.Response(400, request=REQUEST, json={"message": "invalid_request_message_order"})
    status_error = APIStatusError(message="invalid_request_message_order", response=response, body=None)
    provider, _ = harness.provider([], errors=[status_error])

    with pytest.raises(AIServiceRequestError):
        async for _ in provider.generate_stream_with_schema("system prompt", [], {"text": StringField("t")}):
            pass


def _streamed(*contents: str) -> list:
    return [OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(content=content))]) for content in contents] + [
        OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(), finish_reason="stop")], usage=OpenAIUsage()),
    ]


@pytest.mark.asyncio
async def test_a_reply_opening_with_whitespace_before_its_json_is_read_like_any_other() -> None:
    provider, _ = harness.provider([_streamed("\n", " ", '{"signals": {"a": 70}, ', '"text": "hola"}')])
    metadata: dict = {}

    text = "".join([
        chunk async for chunk in provider.generate_stream_with_schema(
            "system prompt", [], {"signals": ObjectField({"a": NumberField()}), "text": StringField("t")},
            on_metadata=lambda name, value: metadata.__setitem__(name, value),
        )
    ])

    assert text == "hola"
    assert metadata["signals"] == {"a": 70.0}


@pytest.mark.asyncio
async def test_a_reply_that_is_not_a_json_object_is_a_malformed_reply() -> None:
    provider, _ = harness.provider([_streamed("```json\n", '{"text": "hola"}', "\n```")])

    with pytest.raises(AIServiceProviderMalformedReplyError):
        async for _ in provider.generate_stream_with_schema("system prompt", [], {"text": StringField("t")}):
            pass
