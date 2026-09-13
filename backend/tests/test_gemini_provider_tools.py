"""Gemini-specific tool-calling behaviour: the "respond as a tool"
fallback (response_schema and tools don't reliably combine here, so a
synthetic "respond" tool, forced via tool_config, carries the structured
answer), functionCall/functionResponse history translation, thought
signature replay, system_instruction concatenation and cache accounting.
The provider-neutral loop is covered once for every provider in
test_provider_tools.py.
"""
from __future__ import annotations

import pytest
from google.genai import types

from ai.ai_service import AiService
from ai.llm_provider import SystemPrompt, ToolCall, ToolCallsRequested
from provider_tools_helpers import (
    SELECT_SPEC, FakeToolSet, GeminiCandidate, GeminiChunk, GeminiContent, GeminiFunctionCall, GeminiHarness, GeminiPart,
    GeminiUsage, drain,
)

harness = GeminiHarness()


async def _raise_tool_calls(responses, history=()) -> ToolCallsRequested:
    provider, _ = harness.provider([responses])
    with pytest.raises(ToolCallsRequested) as raised:
        await drain(provider.generate_stream_with_schema("sys", list(history), {"text": "t"}, tools=[SELECT_SPEC]))
    return raised.value


async def test_a_plain_text_answer_still_streams_when_tools_are_offered_and_a_call_with_no_id_gets_one_generated():
    provider, _ = harness.provider([harness.text_response('{"text": "no lookup needed"}')])
    assert await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[SELECT_SPEC])) == '{"text": "no lookup needed"}'

    requested = await _raise_tool_calls(harness.function_call_response("source_flights_select", {"value": "paris"}, call_id=None))
    assert requested.calls[0].id


async def test_build_contents_round_trips_the_neutral_history_grouping_a_rounds_results_into_one_user_turn():
    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])
    history = [
        {"role": "user", "content": "where's my flight?"},
        {
            "role": "assistant",
            "tool_calls": [
                ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"}),
                ToolCall(id="call_2", name="source_flights_select", arguments={"value": "berlin"}),
            ],
            "content": "Checking...",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "paris row"},
        {"role": "tool", "tool_call_id": "call_2", "content": "berlin row"},
    ]

    await drain(provider.generate_stream_with_schema("sys", history, {"text": "t"}))

    contents = harness.calls(fake_client)[0]["contents"]
    assert len(contents) == 3
    assert contents[0].role == "user" and contents[0].parts[0].text == "where's my flight?"
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "Checking..."
    assert contents[1].parts[1].function_call.name == "source_flights_select"
    assert contents[1].parts[1].function_call.args == {"value": "paris"}
    assert contents[1].parts[1].thought_signature is None
    assert contents[2].role == "user"
    assert len(contents[2].parts) == 2
    assert contents[2].parts[0].function_response.name == "source_flights_select"
    assert contents[2].parts[0].function_response.response == {"result": "paris row"}
    assert contents[2].parts[1].function_response.response == {"result": "berlin row"}


async def test_the_respond_fallback_is_declared_alongside_the_real_tools_and_forced():
    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])

    await drain(provider.generate_stream_with_schema(
        "sys", [], {"text": "the reply", "env": "context"}, tools=[SELECT_SPEC],
    ))

    call = harness.calls(fake_client)[0]
    assert harness.declared_names(call) == {"source_flights_select", "respond"}
    harness.assert_declaration_shape(call, SELECT_SPEC)


async def test_the_respond_fallback_is_parsed_exactly_like_a_schema_response():
    provider, _ = harness.provider([harness.function_call_response("respond", {"text": "hi there", "env": "k: v"})])
    reported: dict[str, str] = {}
    chunks = [
        chunk async for chunk in AiService(provider).generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: reported.__setitem__(k, v),
            schema={"text": "the reply", "env": "context"}, tool_set=FakeToolSet([SELECT_SPEC], results=[]),
        )
    ]

    assert "".join(chunks) == "hi there"
    assert reported.get("env") == "k: v"


async def test_a_tool_calls_thought_signature_is_replayed_verbatim_through_the_history():
    """Gemini stamps every functionCall part with an opaque thought_signature
    and rejects the next request unless that exact part comes back in the
    model turn preceding the functionResponse — so the provider hands its
    own parts back through assistant_content and replays them verbatim.
    https://ai.google.dev/gemini-api/docs/thought-signatures"""
    provider, fake_client = harness.provider([
        harness.function_call_response("source_flights_select", {"value": "VY3003"}, call_id="c1", thought_signature=b"opaque-sig"),
        harness.text_response('{"text": "VY3003 is on time."}'),
    ])
    with pytest.raises(ToolCallsRequested) as raised:
        await drain(provider.generate_stream_with_schema(
            "sys", [{"role": "user", "content": "hi"}], {"text": "t"}, tools=[SELECT_SPEC],
        ))
    requested = raised.value
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": requested.calls, "content": requested.assistant_content},
        {"role": "tool", "tool_call_id": requested.calls[0].id, "content": "row"},
    ]

    await drain(provider.generate_stream_with_schema("sys", history, {"text": "t"}, tools=[SELECT_SPEC]))

    contents = harness.calls(fake_client)[1]["contents"]
    model_turn = contents[1]
    assert model_turn.role == "model"
    assert model_turn.parts[0].function_call.name == "source_flights_select"
    assert model_turn.parts[0].function_call.args == {"value": "VY3003"}
    assert model_turn.parts[0].thought_signature == b"opaque-sig"
    assert contents[2].parts[0].function_response.name == "source_flights_select"


async def test_a_signature_streamed_on_its_own_chunk_and_text_streamed_alongside_land_on_the_replayed_parts_in_order():
    alone = await _raise_tool_calls([
        GeminiChunk(candidates=[GeminiCandidate(content=GeminiContent(parts=[GeminiPart(thought_signature=b"sig-alone")]))]),
        GeminiChunk(candidates=[GeminiCandidate(
            content=GeminiContent(parts=[GeminiPart(function_call=GeminiFunctionCall(name="source_flights_select", args={"value": "VY1"}, id="c1"))]),
            finish_reason=types.FinishReason.STOP,
        )], usage_metadata=GeminiUsage()),
    ])
    parts = alone.assistant_content["gemini_parts"]
    assert len(parts) == 1
    assert parts[0].function_call.name == "source_flights_select"
    assert parts[0].thought_signature == b"sig-alone"

    with_text = await _raise_tool_calls([
        GeminiChunk(candidates=[GeminiCandidate(content=GeminiContent(parts=[GeminiPart(text="Let me ")]))]),
        GeminiChunk(candidates=[GeminiCandidate(content=GeminiContent(parts=[GeminiPart(text="check.")]))]),
        GeminiChunk(candidates=[GeminiCandidate(
            content=GeminiContent(parts=[GeminiPart(function_call=GeminiFunctionCall(name="source_flights_select", args={"value": "VY1"}), thought_signature=b"s")]),
            finish_reason=types.FinishReason.STOP,
        )], usage_metadata=GeminiUsage()),
    ])
    parts = with_text.assistant_content["gemini_parts"]
    assert [p.text for p in parts] == ["Let me check.", None]
    assert parts[1].function_call.name == "source_flights_select" and parts[1].thought_signature == b"s"


async def test_system_prompt_is_sent_as_is_or_as_stable_then_volatile_concatenated():
    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')] * 2)

    await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))
    await drain(provider.generate_stream_with_schema(SystemPrompt(stable="stable part", volatile="volatile part"), [], {"text": "t"}))

    plain, split = (call["config"].system_instruction for call in fake_client.aio.models.calls)
    assert plain == "sys"
    assert split == "stable part\n\nvolatile part"


async def test_cache_read_tokens_are_reported_kept_across_chunks_that_omit_them_and_default_to_zero():
    async def _events(chunks) -> list[tuple[str, object]]:
        provider, _ = harness.provider([chunks])
        events: list[tuple[str, object]] = []
        await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))
        return events

    reported = await _events([GeminiChunk(
        candidates=[GeminiCandidate(finish_reason=types.FinishReason.STOP)],
        usage_metadata=GeminiUsage(prompt_token_count=50, candidates_token_count=5, cached_content_token_count=40),
        text='{"text": "hi"}',
    )])
    assert ("cache_read_tokens", 40) in reported
    assert ("cache_creation_tokens", 0) in reported
    assert ("input_tokens", 50) in reported

    kept = await _events([
        GeminiChunk(usage_metadata=GeminiUsage(prompt_token_count=50, candidates_token_count=1, cached_content_token_count=40)),
        GeminiChunk(
            candidates=[GeminiCandidate(finish_reason=types.FinishReason.STOP)],
            usage_metadata=GeminiUsage(prompt_token_count=50, candidates_token_count=5),
            text='{"text": "hi"}',
        ),
    ])
    assert ("cache_read_tokens", 40) in kept

    absent = await _events(harness.text_response('{"text": "hi"}'))
    assert ("cache_read_tokens", 0) in absent
    assert ("cache_creation_tokens", 0) in absent
