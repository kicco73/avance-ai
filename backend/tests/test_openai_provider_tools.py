"""OpenAI-compatible-specific tool-calling behaviour: the neutral history
<-> tool_calls/role:"tool" translation. The provider-neutral loop is
covered once for every provider in test_provider_tools.py.
"""
from __future__ import annotations

import pytest

from ai.llm_provider import AIServiceError, ToolCall, ToolCallsRequested
from ai.response_schema import StringField
from provider_tools_helpers import OpenAIHarness, drain

harness = OpenAIHarness()


async def test_build_messages_round_trips_the_neutral_tool_history_shapes():
    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])
    history = [
        {"role": "user", "content": "where's my flight?"},
        {
            "role": "assistant",
            "tool_calls": [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})],
            "content": "Checking...",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "city,country\nParis,France\n"},
    ]

    await drain(provider.generate_stream_with_schema("sys", history, {"text": StringField("t")}))

    assert harness.calls(fake_client)[0]["messages"][1:] == [
        {"role": "user", "content": "where's my flight?"},
        {
            "role": "assistant",
            "content": "Checking...",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "source_flights_select", "arguments": '{"value": "paris"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "city,country\nParis,France\n"},
    ]


async def test_tool_calls_are_requested_whatever_finish_reason_the_server_closes_them_with():
    provider, _ = harness.provider([harness.tool_call_response("call_1", "source_flights_select", {"value": "paris"}, finish_reason="stop")])

    with pytest.raises(ToolCallsRequested) as requested:
        await drain(provider.generate_stream_with_schema("sys", [{"role": "user", "content": "hi"}], {"text": StringField("t")}))

    assert [(call.id, call.name, call.arguments) for call in requested.value.calls] == [
        ("call_1", "source_flights_select", {"value": "paris"}),
    ]


async def test_a_tool_call_the_server_sent_without_an_id_is_given_one():
    provider, _ = harness.provider([harness.tool_call_response(None, "source_flights_select", {"value": "paris"})])

    with pytest.raises(ToolCallsRequested) as requested:
        await drain(provider.generate_stream_with_schema("sys", [{"role": "user", "content": "hi"}], {"text": StringField("t")}))

    assert requested.value.calls[0].id


async def test_malformed_tool_call_arguments_are_a_provider_error():
    provider, _ = harness.provider([harness.tool_call_response("call_1", "source_flights_select", {}, arguments_json='{"value": ')])

    with pytest.raises(AIServiceError):
        await drain(provider.generate_stream_with_schema("sys", [{"role": "user", "content": "hi"}], {"text": StringField("t")}))
