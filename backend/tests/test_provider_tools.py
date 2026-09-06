"""Native tool-calling, provider-neutral half: ToolSpec -> each SDK's own
declaration, ToolCallsRequested when the model calls a tool, the neutral
tool history shapes replayed on the next round, AiService's own loop
(MAX_TOOL_ROUNDS, sequential resolution, a failed ToolSet.call still
completing the turn) and ai-must-read-sources forcing — every case run
once per provider through its harness in provider_tools_helpers.py, with
the SDK client stubbed and never a real network call. SDK-specific
behaviour lives in test_<provider>_provider_tools.py.
"""
from __future__ import annotations

import pytest

from ai.ai_service import AiService, MAX_TOOL_ROUNDS, _TOOL_ERROR_DIRECTIVE
from ai.llm_provider import ToolCall, ToolCallsRequested
from provider_tools_helpers import HARNESSES, SELECT_SPEC, TICKETS_SPEC, FakeToolSet, drain


@pytest.fixture(params=HARNESSES, ids=[h.name for h in HARNESSES])
def harness(request):
    return request.param


async def _run(ai_service: AiService, tool_set: FakeToolSet, history=(), **kwargs) -> str:
    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", list(history), on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set, **kwargs,
        )
    ]
    return "".join(chunks)


async def test_no_tools_streams_normally_and_never_sends_a_tools_declaration(harness):
    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])

    out = await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))

    assert out == '{"text": "hi"}'
    harness.assert_no_tools_sent(harness.calls(fake_client)[0])


async def test_a_tool_call_raises_ToolCallsRequested_after_declaring_the_tool_in_the_sdks_own_shape(harness):
    provider, fake_client = harness.provider([harness.tool_call_response("call_1", "source_flights_select", {"value": "paris"})])

    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[SELECT_SPEC]):
            pass

    assert exc_info.value.calls == [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})]
    harness.assert_declaration_shape(harness.calls(fake_client)[0], SELECT_SPEC)


async def test_one_tool_call_is_resolved_and_replayed_before_the_final_response_including_an_error_result(harness):
    provider, fake_client = harness.provider([
        harness.tool_call_response("call_1", "source_flights_select", {"value": "paris"}),
        harness.text_response('{"text": "Paris it is."}'),
    ])
    tool_set = FakeToolSet([SELECT_SPEC], results=["city,country\nParis,France\n"])

    out = await _run(AiService(provider), tool_set, history=[{"role": "user", "content": "where's my flight?"}])

    assert out == "Paris it is."
    assert tool_set.calls == [("source_flights_select", {"value": "paris"})]
    calls = harness.calls(fake_client)
    assert len(calls) == 2
    assert harness.assistant_tool_call_name(calls[1]) == "source_flights_select"
    assert harness.tool_result_content(calls[1]) == "city,country\nParis,France\n"

    provider, fake_client = harness.provider([
        harness.tool_call_response("call_1", "source_flights_select", {"value": "paris"}),
        harness.text_response('{"text": "Sorry, lookup failed."}'),
    ])
    tool_set = FakeToolSet([SELECT_SPEC], results=["error: unknown tool 'source_flights_select'."])

    out = await _run(AiService(provider), tool_set)

    assert out == "Sorry, lookup failed."
    assert harness.tool_result_content(harness.calls(fake_client)[1]) == "error: unknown tool 'source_flights_select'." + _TOOL_ERROR_DIRECTIVE


async def test_rounds_are_resolved_sequentially_and_exceeding_max_rounds_forces_a_final_answer(harness):
    provider, fake_client = harness.provider([
        harness.tool_call_response("call_1", "source_flights_select", {"value": "paris"}),
        harness.tool_call_response("call_2", "source_flights_select", {"value": "berlin"}),
        harness.text_response('{"text": "Found both."}'),
    ])
    tool_set = FakeToolSet([SELECT_SPEC], results=["paris row", "berlin row"])

    out = await _run(AiService(provider), tool_set)

    assert out == "Found both."
    assert tool_set.calls == [("source_flights_select", {"value": "paris"}), ("source_flights_select", {"value": "berlin"})]
    assert len(harness.calls(fake_client)) == 3

    provider, fake_client = harness.provider([
        *(harness.tool_call_response(f"call_{i}", "source_flights_select", {"value": "x"}) for i in range(MAX_TOOL_ROUNDS)),
        harness.text_response('{"text": "Best I can tell you without more lookups."}'),
    ])
    tool_set = FakeToolSet([SELECT_SPEC], results=["row"] * MAX_TOOL_ROUNDS)

    out = await _run(AiService(provider), tool_set)

    assert out == "Best I can tell you without more lookups."
    assert len(harness.calls(fake_client)) == MAX_TOOL_ROUNDS + 1
    assert len(tool_set.calls) == MAX_TOOL_ROUNDS


async def test_required_tools_restricts_and_forces_the_callable_set_while_none_sends_the_full_catalog_unforced(harness):
    provider, fake_client = harness.provider([harness.text_response('{"text": "ok"}'), harness.text_response('{"text": "ok"}')])

    await drain(provider.generate_stream_with_schema(
        "sys", [], {"text": "t"}, tools=[SELECT_SPEC, TICKETS_SPEC], required_tools=[SELECT_SPEC],
    ))
    await drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[SELECT_SPEC, TICKETS_SPEC]))

    forced, unforced = harness.calls(fake_client)
    assert harness.forced_names(forced) == ["source_flights_select"]
    assert harness.forced_names(unforced) is None
    assert harness.declared_names(unforced) == {"source_flights_select", "source_tickets_select"} | harness.synthetic_tools


async def test_forcing_applies_to_round_one_only_and_never_without_the_flag_or_without_a_must_source(harness):
    def _responses():
        return [
            harness.tool_call_response("call_1", "source_flights_select", {"value": "paris"}),
            harness.text_response('{"text": "Paris it is."}'),
        ]

    provider, fake_client = harness.provider(_responses())
    tool_set = FakeToolSet([SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[SELECT_SPEC])
    await _run(AiService(provider), tool_set, force_required_tools=True)
    round_1, round_2 = harness.calls(fake_client)
    assert harness.forced_names(round_1) == ["source_flights_select"]
    assert harness.forced_names(round_2) is None

    provider, fake_client = harness.provider(_responses())
    tool_set = FakeToolSet([SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[SELECT_SPEC])
    await _run(AiService(provider), tool_set)
    assert harness.forced_names(harness.calls(fake_client)[0]) is None

    provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])
    await _run(AiService(provider), FakeToolSet([SELECT_SPEC], results=[], required_specs=[]), force_required_tools=True)
    assert harness.forced_names(harness.calls(fake_client)[0]) is None
