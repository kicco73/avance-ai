"""Native tool-calling — Anthropic-side translation (ToolSpec ->
input_schema, ToolCallsRequested on stop_reason == "tool_use", the two
neutral tool history shapes <-> Anthropic's own tool_use/tool_result
blocks) and AiService's own tool-call loop (MAX_TOOL_ROUNDS, sequential
resolution, a failed ToolSet.call still completing the turn) — with the
Anthropic SDK's async client stubbed, never a real network call.
"""
from __future__ import annotations

import pytest

from ai.ai_service import AiService, MAX_TOOL_ROUNDS
from ai._providers.anthropic_provider_v2 import AnthropicProvider
from ai.llm_provider import AIServiceConfig, AIServiceRequestError, ToolCall, ToolCallsRequested, ToolSpec
from db.models import AiTokenUsage

_SELECT_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)


class _FakeUsage:
    def __init__(self, input_tokens: int = 2, output_tokens: int = 1) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


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
    """Enough of tracking.sources.ToolSet's own contract (specs()/call())
    for AiService's loop to drive — call() never raises, matching the
    real one's contract, and just returns whatever's queued next."""

    def __init__(self, specs: list[ToolSpec], results: list[str]) -> None:
        self._specs = specs
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def status_text(self, name: str) -> str:
        return f"Searching {name}…"

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


async def test_a_tool_call_emits_a_tool_call_event_then_a_tool_result_event():
    """See ai_service.py's own tool-call loop: 'tool_call' fires before
    the call itself (status_text only, for a live transient bubble
    line), 'tool_result' fires after (the durable {name, arguments,
    result} record TrackingProcessor persists to Tracking.tool_calls)."""
    tool_call_response = _FakeFinalMessage(
        "tool_use", content=[_FakeToolUseBlock("call_1", "source_flights_select", {"value": "paris"})],
    )
    final_response = _FakeFinalMessage("end_turn")
    provider, _ = _provider([([], tool_call_response), (['{"text": "Paris it is."}'], final_response)])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"])
    events: list[tuple[str, object]] = []

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [{"role": "user", "content": "where's my flight?"}],
        on_metadata=lambda k, v: events.append((k, v)), schema={"text": "t"}, tool_set=tool_set,
    ):
        pass

    tool_events = [event for event in events if event[0] in ("tool_call", "tool_result")]
    assert tool_events == [
        ("tool_call", {"status_text": "Searching source_flights_select…"}),
        ("tool_result", {
            "name": "source_flights_select", "arguments": {"value": "paris"},
            "result": "city,country\nParis,France\n",
        }),
    ]
    # 'tool_call' strictly before 'tool_result' — the frontend's own
    # transient line must appear, then clear, never the other way round.
    assert [event[0] for event in events].index("tool_call") < [event[0] for event in events].index("tool_result")


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


# (d) exceeding MAX_TOOL_ROUNDS.
async def test_exceeding_max_tool_rounds_raises_a_clear_error():
    always_tool_use = _FakeFinalMessage("tool_use", content=[_FakeToolUseBlock("call", "source_flights_select", {"value": "x"})])
    provider, fake_client = _provider([([], always_tool_use) for _ in range(MAX_TOOL_ROUNDS)])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["row"] * MAX_TOOL_ROUNDS)

    with pytest.raises(AIServiceRequestError, match=str(MAX_TOOL_ROUNDS)):
        async for _ in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        ):
            pass

    assert len(fake_client.messages.calls) == MAX_TOOL_ROUNDS
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
    assert second_call_history[-1]["content"][0]["content"] == "error: unknown tool 'source_flights_select'."


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
