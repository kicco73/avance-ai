"""Native tool-calling — Anthropic-side translation (ToolSpec ->
input_schema, ToolCallsRequested on stop_reason == "tool_use", the two
neutral tool history shapes <-> Anthropic's own tool_use/tool_result
blocks) and AiService's own tool-call loop (MAX_TOOL_ROUNDS, sequential
resolution, a failed ToolSet.call still completing the turn) — with the
Anthropic SDK's async client stubbed, never a real network call.
"""
from __future__ import annotations

import pytest

from ai.ai_service import AiService, MAX_TOOL_ROUNDS, _TOOL_ERROR_DIRECTIVE
from ai._providers.anthropic_provider_v2 import AnthropicProvider
from ai.llm_provider import AIServiceConfig, SystemPrompt, ToolCall, ToolCallsRequested, ToolSpec
from db.models import AiTokenUsage

_SELECT_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)

_TICKETS_SPEC = ToolSpec(
    name="source_tickets_select",
    description="Grep over the tickets archive.",
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)


class _FakeUsage:
    def __init__(
        self, input_tokens: int = 2, output_tokens: int = 1,
        cache_read_input_tokens: int | None = None, cache_creation_input_tokens: int | None = None,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        # None (the default) matches a real anthropic.types.Usage that
        # never populated these — the provider must fall back to 0 rather
        # than propagate a None into a token count.
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict) -> None:
        self.id = id
        self.name = name
        self.input = input


class _FakeFinalMessage:
    def __init__(self, stop_reason: str, content=(), usage: _FakeUsage | None = None) -> None:
        self.stop_reason = stop_reason
        self.content = list(content)
        self.usage = usage or _FakeUsage()


class _FakeMessageStream:
    """Stands in for anthropic's own MessageStreamManager/AsyncMessageStream
    — an async context manager exposing `.text_stream` (an async
    iterator) and `.get_final_message()`, the only two surface points
    AnthropicProvider actually touches."""

    def __init__(self, text_chunks: list[str], final_message: _FakeFinalMessage) -> None:
        self._text_chunks = text_chunks
        self._final_message = final_message

    async def __aenter__(self) -> "_FakeMessageStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    @property
    def text_stream(self):
        return self._iter_text()

    async def _iter_text(self):
        for chunk in self._text_chunks:
            yield chunk

    async def get_final_message(self) -> _FakeFinalMessage:
        return self._final_message


class _FakeMessagesResource:
    def __init__(self, responses: list[tuple[list[str], _FakeFinalMessage]]) -> None:
        self._responses = list(responses)
        # One kwargs dict per .stream() call — inspected by the round-trip
        # test below to check exactly what was actually sent.
        self.calls: list[dict] = []

    def stream(self, **kwargs) -> _FakeMessageStream:
        self.calls.append(kwargs)
        text_chunks, final_message = self._responses.pop(0)
        return _FakeMessageStream(text_chunks, final_message)


class _FakeAsyncClient:
    def __init__(self, responses: list[tuple[list[str], _FakeFinalMessage]]) -> None:
        self.messages = _FakeMessagesResource(responses)


class _FakeToolSet:
    """Enough of tracking.sources.ToolSet's own contract (specs()/call()/
    required_specs()/session_id) for AiService's loop to
    drive — call() never raises, matching the real one's contract, and
    just returns whatever's queued next. `required_specs`: the
    ai-must-read-sources subset (see ToolSet.required_specs) — empty by
    default, so every existing test (none of which exercises forcing)
    behaves exactly as it did before this parameter existed."""

    def __init__(
        self, specs: list[ToolSpec], results: list[str], required_specs: list[ToolSpec] | None = None,
    ) -> None:
        self._specs = specs
        self._results = list(results)
        self._required_specs = required_specs or []
        self.calls: list[tuple[str, dict]] = []
        self.session_id = 99

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def required_specs(self) -> list[ToolSpec]:
        return self._required_specs

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self._results.pop(0)


def _provider(responses: list[tuple[list[str], _FakeFinalMessage]]) -> tuple[AnthropicProvider, _FakeAsyncClient]:
    provider = AnthropicProvider(AIServiceConfig("anthropic", "claude-x", "k", None, "x"))
    fake_client = _FakeAsyncClient(responses)
    provider._async_client_for_current_loop = lambda: fake_client  # type: ignore[method-assign]
    return provider, fake_client


async def _drain(stream) -> str:
    out = ""
    async for chunk in stream:
        out += chunk
    return out


# (a) a response with no tools involved streams normally, and the SDK
# call never even carries a `tools` kwarg — a state with no `tools:`
# must produce the exact same request as before tool-calling existed.
async def test_no_tools_streams_normally_and_never_sends_a_tools_kwarg():
    final = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "hi"}'], final)])

    out = await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))

    assert out == '{"text": "hi"}'
    assert "tools" not in fake_client.messages.calls[0]


async def test_a_tool_use_stop_reason_raises_ToolCallsRequested_with_calls_and_preceding_text():
    final = _FakeFinalMessage(
        "tool_use",
        content=[_FakeTextBlock("Let me check."), _FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
    )
    provider, fake_client = _provider([([], final)])

    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]):
            pass

    requested = exc_info.value
    assert requested.calls == [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})]
    assert requested.assistant_content == "Let me check."
    sent_tools = fake_client.messages.calls[0]["tools"]
    assert sent_tools == [{
        "name": "source_flights_select", "description": "Grep over the flights archive.",
        "input_schema": _SELECT_SPEC.parameters,
    }]


async def test_a_tool_use_response_with_no_preceding_text_reports_assistant_content_as_none():
    final = _FakeFinalMessage("tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "x"})])
    provider, _ = _provider([([], final)])

    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]):
            pass

    assert exc_info.value.assistant_content is None


# (b) one tool call, then the real response.
async def test_ai_service_resolves_one_tool_call_then_streams_the_final_response():
    tool_call_response = _FakeFinalMessage(
        "tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
    )
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([
        ([], tool_call_response),
        (['{"text": "Paris it is."}'], final_response),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"])

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [{"role": "user", "content": "where's my flight?"}],
            on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Paris it is."
    assert tool_set.calls == [("source_flights_select", {"value": "paris"})]
    assert len(fake_client.messages.calls) == 2
    # The second call's own history carries the assistant's tool_calls
    # message, then one 'tool' result message per call, appended in
    # order — never written back into the original `history` list.
    second_call_history = fake_client.messages.calls[1]["messages"]
    assert second_call_history[-2]["role"] == "assistant"
    assert second_call_history[-2]["content"][-1]["type"] == "tool_use"
    assert second_call_history[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "city,country\nParis,France\n"}],
    }




# (c) two rounds before the real response.
async def test_ai_service_resolves_two_tool_call_rounds_before_the_final_response():
    round_1 = _FakeFinalMessage("tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})])
    round_2 = _FakeFinalMessage("tool_use", content=[_FakeToolUseBlock("call_2", "source_flights_select", {"value": "berlin"})])
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([([], round_1), ([], round_2), (['{"text": "Found both."}'], final_response)])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["paris row", "berlin row"])

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Found both."
    assert tool_set.calls == [
        ("source_flights_select", {"value": "paris"}),
        ("source_flights_select", {"value": "berlin"}),
    ]
    assert len(fake_client.messages.calls) == 3


async def test_the_token_usage_tap_never_pairs_a_round_s_input_with_a_stale_output(db):
    """Regression: without resetting `captured` between rounds (see
    AiService._tap_token_usage), round 2's own input_tokens would
    momentarily pair with round 1's still-cached output_tokens (and
    round 3's with round 2's), writing an extra, wrong AiTokenUsage row
    before the correct pair overwrote it — inflating summed usage totals."""
    round_1 = _FakeFinalMessage(
        "tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
        usage=_FakeUsage(input_tokens=10, output_tokens=20),
    )
    round_2 = _FakeFinalMessage(
        "tool_use", content=[_FakeToolUseBlock("call_2", "source_flights_select", {"value": "berlin"})],
        usage=_FakeUsage(input_tokens=30, output_tokens=40),
    )
    final_response = _FakeFinalMessage("end_turn", usage=_FakeUsage(input_tokens=50, output_tokens=60))
    provider, _ = _provider([([], round_1), ([], round_2), (['{"text": "Found both."}'], final_response)])
    ai_service = AiService(provider, db=db)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["paris row", "berlin row"])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        pass

    pairs = sorted((row.input_tokens, row.output_tokens) for row in AiTokenUsage.select())
    assert pairs == [(10, 20), (30, 40), (50, 60)]


# (d) exceeding MAX_TOOL_ROUNDS: tools are dropped for one final round so
# the model must answer instead of the turn raising.
async def test_exceeding_max_tool_rounds_forces_a_final_answer_with_tools_disabled():
    always_tool_use = _FakeFinalMessage("tool_use", content=[_FakeToolUseBlock("call", "source_flights_select", {"value": "x"})])
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([
        *(([], always_tool_use) for _ in range(MAX_TOOL_ROUNDS)),
        (['{"text": "Best I can tell you without more lookups."}'], final_response),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["row"] * MAX_TOOL_ROUNDS)

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Best I can tell you without more lookups."
    assert len(fake_client.messages.calls) == MAX_TOOL_ROUNDS + 1
    assert len(tool_set.calls) == MAX_TOOL_ROUNDS


# (e) ToolSet.call itself never raises (its own contract — see
# test_tool_set.py) but can report a failure as an "error: ..." string;
# AiService's loop treats that exactly like any other tool result and
# the turn still completes normally.
async def test_a_failed_tool_lookup_feeds_an_error_string_back_and_the_turn_still_completes():
    tool_call_response = _FakeFinalMessage("tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})])
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([([], tool_call_response), (['{"text": "Sorry, lookup failed."}'], final_response)])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["error: unknown tool 'source_flights_select'."])

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Sorry, lookup failed."
    second_call_history = fake_client.messages.calls[1]["messages"]
    assert second_call_history[-1]["content"][0]["content"] == "error: unknown tool 'source_flights_select'." + _TOOL_ERROR_DIRECTIVE


# (f) round-trip: the neutral tool history shapes translate to/from
# Anthropic's own wire format exactly.
def test_build_messages_round_trips_the_neutral_tool_history_shapes():
    provider, _ = _provider([])
    history = [
        {"role": "user", "content": "where's my flight?"},
        {
            "role": "assistant",
            "tool_calls": [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})],
            "content": "Checking...",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "city,country\nParis,France\n"},
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
    ]


def test_build_messages_an_assistant_tool_calls_message_with_no_text_carries_no_text_block():
    provider, _ = _provider([])
    history = [
        {"role": "assistant", "tool_calls": [ToolCall(id="call_1", name="source_flights_read", arguments={})], "content": None},
    ]

    messages = provider._build_messages(history)

    assert messages == [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "source_flights_read", "input": {}}]},
    ]


# (g) ai-must-read-sources forcing — see AiService.generate_stream_with_
# metadata's own force_required_tools/required_tools and
# TrackingProcessor.force_required_tools_for, which decides it. The
# provider itself only ever translates "required_tools was given" into
# its own tool_choice dialect; it never decides *whether* to force.
async def test_required_tools_restricts_tools_and_forces_tool_choice_any():
    final = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "ok"}'], final)])

    await _drain(provider.generate_stream_with_schema(
        "sys", [], {"text": "t"}, tools=[_SELECT_SPEC, _TICKETS_SPEC], required_tools=[_SELECT_SPEC],
    ))

    sent = fake_client.messages.calls[0]
    assert sent["tools"] == [{
        "name": "source_flights_select", "description": "Grep over the flights archive.",
        "input_schema": _SELECT_SPEC.parameters,
    }]
    assert sent["tool_choice"] == {"type": "any"}


async def test_no_required_tools_sends_the_full_catalog_with_no_tool_choice():
    final = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "ok"}'], final)])

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC, _TICKETS_SPEC]))

    sent = fake_client.messages.calls[0]
    assert {t["name"] for t in sent["tools"]} == {"source_flights_select", "source_tickets_select"}
    assert "tool_choice" not in sent


async def test_first_round_of_a_forced_turn_restricts_tool_choice_then_round_two_is_auto():
    tool_call_response = _FakeFinalMessage(
        "tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
    )
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([
        ([], tool_call_response), (['{"text": "Paris it is."}'], final_response),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[_SELECT_SPEC])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        force_required_tools=True,
    ):
        pass

    round_1, round_2 = fake_client.messages.calls
    assert round_1["tool_choice"] == {"type": "any"}
    assert round_1["tools"] == [{
        "name": "source_flights_select", "description": "Grep over the flights archive.",
        "input_schema": _SELECT_SPEC.parameters,
    }]
    assert "tool_choice" not in round_2


async def test_force_required_tools_false_never_restricts_even_with_a_must_source():
    """The second turn in the same state (TrackingProcessor.
    force_required_tools_for returns False) — auto from round 1, even
    though this state does declare an ai-must-read-sources tool."""
    tool_call_response = _FakeFinalMessage(
        "tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
    )
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([
        ([], tool_call_response), (['{"text": "Paris it is."}'], final_response),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[_SELECT_SPEC])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        pass

    assert "tool_choice" not in fake_client.messages.calls[0]


async def test_a_state_with_only_ai_may_read_sources_is_never_forced():
    """required_specs() empty (no ai-must-read-sources at all for this
    state) — force_required_tools=True has nothing to restrict to, so
    round 1 stays auto regardless."""
    final_response = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "hi"}'], final_response)])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=[], required_specs=[])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        force_required_tools=True,
    ):
        pass

    assert "tool_choice" not in fake_client.messages.calls[0]


# (h) SystemPrompt splitting — one cache breakpoint on `stable` alone; see
# ai.llm_provider.SystemPrompt and AnthropicProvider._build_system.
async def test_a_plain_str_system_prompt_produces_one_cached_block():
    final = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "hi"}'], final)])

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))

    sent_system = fake_client.messages.calls[0]["system"]
    assert sent_system == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]


async def test_a_system_prompt_with_no_volatile_tail_produces_one_cached_block():
    final = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "hi"}'], final)])

    await _drain(provider.generate_stream_with_schema(SystemPrompt(stable="sys"), [], {"text": "t"}))

    sent_system = fake_client.messages.calls[0]["system"]
    assert sent_system == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]


async def test_a_system_prompt_with_a_volatile_tail_produces_two_blocks_cache_on_the_first_only():
    final = _FakeFinalMessage("end_turn")
    provider, fake_client = _provider([(['{"text": "hi"}'], final)])

    await _drain(provider.generate_stream_with_schema(
        SystemPrompt(stable="stable part", volatile="volatile part"), [], {"text": "t"},
    ))

    sent_system = fake_client.messages.calls[0]["system"]
    assert sent_system == [
        {"type": "text", "text": "stable part", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "volatile part"},
    ]


# (i) cache-read/cache-creation token accounting — usage.input_tokens
# excludes cache entirely on this provider, so on_metadata's own
# "input_tokens" must be normalized into a true, cache-inclusive total.
async def test_cache_read_and_creation_tokens_are_normalized_into_the_input_total():
    usage = _FakeUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20)
    final = _FakeFinalMessage("end_turn", usage=usage)
    provider, _ = _provider([(['{"text": "hi"}'], final)])
    events: list[tuple[str, object]] = []

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))

    assert ("cache_read_tokens", 100) in events
    assert ("cache_creation_tokens", 20) in events
    # 10 (excludes cache) + 100 + 20 = 130 — the true, cache-inclusive input.
    assert ("input_tokens", 130) in events
    assert ("output_tokens", 5) in events
    assert provider.get_total_tokens() == 130 + 5


async def test_a_usage_with_no_cache_fields_reports_zero_cache_tokens():
    final = _FakeFinalMessage("end_turn", usage=_FakeUsage(input_tokens=10, output_tokens=5))
    provider, _ = _provider([(['{"text": "hi"}'], final)])
    events: list[tuple[str, object]] = []

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))

    assert ("cache_read_tokens", 0) in events
    assert ("cache_creation_tokens", 0) in events
    assert ("input_tokens", 10) in events


async def test_a_row_is_recorded_with_the_normalized_input_total_and_both_cache_fields(db):
    usage = _FakeUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20)
    final = _FakeFinalMessage("end_turn", usage=usage)
    provider, _ = _provider([(['{"text": "hi"}'], final)])
    ai_service = AiService(provider, db=db)

    async for _ in ai_service.generate_stream_with_metadata("sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}):
        pass

    row = AiTokenUsage.get()
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_creation_tokens) == (130, 5, 100, 20)
