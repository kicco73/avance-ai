"""Anthropic-specific tool-calling behaviour: preceding-text capture on
tool_use, the neutral history <-> tool_use/tool_result block
translation, SystemPrompt cache breakpoints and cache-inclusive token
accounting. The provider-neutral loop is covered once for every
provider in test_provider_tools.py.
"""
from __future__ import annotations

import pytest

from ai.ai_service import AiService
from ai.llm_provider import SystemPrompt, ToolCall, ToolCallsRequested
from db.models import AiTokenUsage
from provider_tools_helpers import (
    SELECT_SPEC, AnthropicFinalMessage, AnthropicHarness, AnthropicTextBlock, AnthropicToolUseBlock, AnthropicUsage,
    FakeToolSet, drain,
)

harness = AnthropicHarness()


async def _raise_tool_calls(final: AnthropicFinalMessage) -> ToolCallsRequested:
    provider, _ = harness.provider([([], final)])
    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[SELECT_SPEC]):
            pass
    return exc_info.value


async def test_a_tool_use_reports_the_preceding_text_as_assistant_content_or_none_without_it():
    with_text = await _raise_tool_calls(AnthropicFinalMessage(
        "tool_use",
        content=[AnthropicTextBlock("Let me check."), AnthropicToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
    ))
    assert with_text.assistant_content == "Let me check."

    without_text = await _raise_tool_calls(AnthropicFinalMessage(
        "tool_use", content=[AnthropicToolUseBlock("call_1", "source_flights_select", {"value": "x"})],
    ))
    assert without_text.assistant_content is None


def test_build_messages_round_trips_the_neutral_tool_history_shapes_omitting_an_empty_text_block():
    provider, _ = harness.provider([])
    history = [
        {"role": "user", "content": "where's my flight?"},
        {
            "role": "assistant",
            "tool_calls": [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})],
            "content": "Checking...",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "city,country\nParis,France\n"},
        {"role": "assistant", "tool_calls": [ToolCall(id="call_2", name="source_flights_read", arguments={})], "content": None},
    ]

    messages = provider._build_messages(history)

    assert messages == [
        {"role": "user", "content": "where's my flight?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking..."},
                {"type": "tool_use", "id": "call_1", "name": "source_flights_select", "input": {"value": "paris"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "city,country\nParis,France\n"}],
        },
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_2", "name": "source_flights_read", "input": {}}]},
    ]


async def test_the_token_usage_tap_never_pairs_a_round_s_input_with_a_stale_output(db):
    """Regression: without resetting `captured` between rounds (see
    AiService._tap_token_usage), round 2's own input_tokens would
    momentarily pair with round 1's still-cached output_tokens (and
    round 3's with round 2's), writing an extra, wrong AiTokenUsage row
    before the correct pair overwrote it — inflating summed usage totals."""
    round_1 = AnthropicFinalMessage(
        "tool_use", content=[AnthropicToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
        usage=AnthropicUsage(input_tokens=10, output_tokens=20),
    )
    round_2 = AnthropicFinalMessage(
        "tool_use", content=[AnthropicToolUseBlock("call_2", "source_flights_select", {"value": "berlin"})],
        usage=AnthropicUsage(input_tokens=30, output_tokens=40),
    )
    final_response = AnthropicFinalMessage("end_turn", usage=AnthropicUsage(input_tokens=50, output_tokens=60))
    provider, _ = harness.provider([([], round_1), ([], round_2), (['{"text": "Found both."}'], final_response)])
    ai_service = AiService(provider, db=db)
    tool_set = FakeToolSet([SELECT_SPEC], results=["paris row", "berlin row"])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        pass

    pairs = sorted((row.input_tokens, row.output_tokens) for row in AiTokenUsage.select())
    assert pairs == [(10, 20), (30, 40), (50, 60)]


async def test_system_prompt_gets_one_cached_block_plus_an_uncached_volatile_tail_when_present():
    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')] * 3)

    await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))
    await drain(provider.generate_stream_with_schema(SystemPrompt(stable="sys"), [], {"text": "t"}))
    await drain(provider.generate_stream_with_schema(SystemPrompt(stable="stable part", volatile="volatile part"), [], {"text": "t"}))

    plain, stable_only, with_volatile = (call["system"] for call in fake_client.messages.calls)
    assert plain == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert stable_only == plain
    assert with_volatile == [
        {"type": "text", "text": "stable part", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "volatile part"},
    ]


async def test_cache_read_and_creation_tokens_are_normalized_into_the_input_total_defaulting_to_zero():
    usage = AnthropicUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20)
    provider, _ = harness.provider([(['{"text": "hi"}'], AnthropicFinalMessage("end_turn", usage=usage))])
    events: list[tuple[str, object]] = []

    await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))

    assert ("cache_read_tokens", 100) in events
    assert ("cache_creation_tokens", 20) in events
    assert ("input_tokens", 130) in events
    assert ("output_tokens", 5) in events
    assert provider.get_total_tokens() == 130 + 5

    provider, _ = harness.provider([(['{"text": "hi"}'], AnthropicFinalMessage("end_turn", usage=AnthropicUsage(input_tokens=10, output_tokens=5)))])
    events = []

    await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))

    assert ("cache_read_tokens", 0) in events
    assert ("cache_creation_tokens", 0) in events
    assert ("input_tokens", 10) in events


async def test_a_row_is_recorded_with_the_normalized_input_total_and_both_cache_fields(db):
    usage = AnthropicUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20)
    provider, _ = harness.provider([(['{"text": "hi"}'], AnthropicFinalMessage("end_turn", usage=usage))])
    ai_service = AiService(provider, db=db)

    async for _ in ai_service.generate_stream_with_metadata("sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}):
        pass

    row = AiTokenUsage.get()
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_creation_tokens) == (130, 5, 100, 20)
