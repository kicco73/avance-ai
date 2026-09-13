"""OpenAI-compatible-specific tool-calling behaviour: the neutral history
<-> tool_calls/role:"tool" translation. The provider-neutral loop is
covered once for every provider in test_provider_tools.py.
"""
from __future__ import annotations

from ai.llm_provider import ToolCall
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

    await drain(provider.generate_stream_with_schema("sys", history, {"text": "t"}))

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
