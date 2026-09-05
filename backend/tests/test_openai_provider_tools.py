"""Native tool-calling — OpenAI-compatible-side translation (ToolSpec ->
function tool declaration, ToolCallsRequested on finish_reason ==
"tool_calls", the two neutral tool history shapes <-> OpenAI's own
tool_calls/role:"tool" messages) and AiService's own tool-call loop
(MAX_TOOL_ROUNDS, sequential resolution, a failed ToolSet.call still
completing the turn) — with the OpenAI SDK's async client stubbed, never
a real network call.
"""
from __future__ import annotations

import pytest

from ai.ai_service import AiService, MAX_TOOL_ROUNDS
from ai._providers.openai_provider_v2 import OpenAICompatibleProvider
from ai.llm_provider import AIServiceConfig, AIServiceRequestError, ToolCall, ToolCallsRequested, ToolSpec

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
    def __init__(self, total_tokens: int = 3, prompt_tokens: int = 2, completion_tokens: int = 1) -> None:
        self.total_tokens = total_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeDeltaFunction:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _FakeDeltaToolCall:
    def __init__(self, index: int, id: str | None = None, name: str | None = None, arguments: str | None = None) -> None:
        self.index = index
        self.id = id
        self.function = _FakeDeltaFunction(name=name, arguments=arguments)


class _FakeDelta:
    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta: _FakeDelta, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, choices: list | None = None, usage: _FakeUsage | None = None) -> None:
        self.choices = choices or []
        self.usage = usage


class _FakeStream:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _FakeCompletions:
    def __init__(self, responses: list[list[_FakeChunk]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self._responses.pop(0))


class _FakeAsyncOpenAIClient:
    def __init__(self, responses: list[list[_FakeChunk]]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(responses)


class _FakeToolSet:
    """Enough of tracking.sources.ToolSet's own contract (specs()/call()/
    required_specs()/summary_text()/session_id) for AiService's loop to
    drive — call() never raises, matching the real one's contract, and
    just returns whatever's queued next. `required_specs`: the
    ai-must-query-sources subset (see ToolSet.required_specs) — empty by
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

    def status_text(self, name: str) -> str:
        return f"Searching {name}…"

    def summary_text(self, name: str, arguments: dict, result: str) -> str:
        return f"Searched {name} · fake summary"

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self._results.pop(0)


def _provider(responses: list[list[_FakeChunk]]) -> tuple[OpenAICompatibleProvider, _FakeAsyncOpenAIClient]:
    provider = OpenAICompatibleProvider(AIServiceConfig("openai", "gpt-x", "k", None, "x"))
    fake_client = _FakeAsyncOpenAIClient(responses)
    provider._client = fake_client  # type: ignore[assignment]
    return provider, fake_client


def _text_response(text: str, finish_reason: str = "stop") -> list[_FakeChunk]:
    return [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content=text))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(), finish_reason=finish_reason)], usage=_FakeUsage()),
    ]


def _tool_call_response(call_id: str, name: str, arguments_json: str) -> list[_FakeChunk]:
    # Split the JSON arguments across two deltas, matching how a real
    # streamed response dribbles them in over several chunks.
    midpoint = len(arguments_json) // 2
    return [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[_FakeDeltaToolCall(0, id=call_id, name=name)]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[_FakeDeltaToolCall(0, arguments=arguments_json[:midpoint])]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[_FakeDeltaToolCall(0, arguments=arguments_json[midpoint:])]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(), finish_reason="tool_calls")], usage=_FakeUsage()),
    ]


async def _drain(stream) -> str:
    out = ""
    async for chunk in stream:
        out += chunk
    return out


# (a) a response with no tools involved streams normally, and the SDK
# call never even carries a `tools` kwarg — a state with no `tools:`
# must produce the exact same request as before tool-calling existed.
async def test_no_tools_streams_normally_and_never_sends_a_tools_kwarg():
    provider, fake_client = _provider([_text_response('{"text": "hi"}')])

    out = await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))

    assert out == '{"text": "hi"}'
    assert "tools" not in fake_client.chat.completions.calls[0]


async def test_a_tool_calls_finish_reason_raises_ToolCallsRequested_with_the_accumulated_call():
    provider, fake_client = _provider([_tool_call_response("call_1", "source_flights_select", '{"value": "paris"}')])

    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]):
            pass

    requested = exc_info.value
    assert requested.calls == [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})]
    sent_tools = fake_client.chat.completions.calls[0]["tools"]
    assert sent_tools == [{
        "type": "function",
        "function": {
            "name": "source_flights_select", "description": "Grep over the flights archive.",
            "parameters": _SELECT_SPEC.parameters,
        },
    }]


# (b) one tool call, then the real response.
async def test_ai_service_resolves_one_tool_call_then_streams_the_final_response():
    provider, fake_client = _provider([
        _tool_call_response("call_1", "source_flights_select", '{"value": "paris"}'),
        _text_response('{"text": "Paris it is."}'),
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
    assert len(fake_client.chat.completions.calls) == 2
    second_call_messages = fake_client.chat.completions.calls[1]["messages"]
    assert second_call_messages[-2]["role"] == "assistant"
    assert second_call_messages[-2]["tool_calls"][0]["function"]["name"] == "source_flights_select"
    assert second_call_messages[-1] == {
        "role": "tool", "tool_call_id": "call_1", "content": "city,country\nParis,France\n",
    }


# (c) two rounds before the real response.
async def test_ai_service_resolves_two_tool_call_rounds_before_the_final_response():
    provider, fake_client = _provider([
        _tool_call_response("call_1", "source_flights_select", '{"value": "paris"}'),
        _tool_call_response("call_2", "source_flights_select", '{"value": "berlin"}'),
        _text_response('{"text": "Found both."}'),
    ])
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
    assert len(fake_client.chat.completions.calls) == 3


# (d) exceeding MAX_TOOL_ROUNDS.
async def test_exceeding_max_tool_rounds_raises_a_clear_error():
    provider, fake_client = _provider([
        _tool_call_response(f"call_{i}", "source_flights_select", '{"value": "x"}') for i in range(MAX_TOOL_ROUNDS)
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["row"] * MAX_TOOL_ROUNDS)

    with pytest.raises(AIServiceRequestError, match=str(MAX_TOOL_ROUNDS)):
        async for _ in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        ):
            pass

    assert len(fake_client.chat.completions.calls) == MAX_TOOL_ROUNDS
    assert len(tool_set.calls) == MAX_TOOL_ROUNDS


# (e) ToolSet.call itself never raises (its own contract — see
# test_tool_set.py) but can report a failure as an "error: ..." string;
# AiService's loop treats that exactly like any other tool result and
# the turn still completes normally.
async def test_a_failed_tool_lookup_feeds_an_error_string_back_and_the_turn_still_completes():
    provider, fake_client = _provider([
        _tool_call_response("call_1", "source_flights_select", '{"value": "paris"}'),
        _text_response('{"text": "Sorry, lookup failed."}'),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["error: unknown tool 'source_flights_select'."])

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Sorry, lookup failed."
    second_call_messages = fake_client.chat.completions.calls[1]["messages"]
    assert second_call_messages[-1]["content"] == "error: unknown tool 'source_flights_select'."


# (f) round-trip: the neutral tool history shapes translate to/from
# OpenAI's own wire format exactly.
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
            "content": "Checking...",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "source_flights_select", "arguments": '{"value": "paris"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "city,country\nParis,France\n"},
    ]


# (g) ai-must-query-sources forcing — see AiService.generate_stream_with_
# metadata's own force_required_tools/required_tools and
# TrackingProcessor.force_required_tools_for, which decides it. The
# provider itself only ever translates "required_tools was given" into
# its own tool_choice dialect; it never decides *whether* to force.
async def test_required_tools_restricts_tools_and_forces_tool_choice_required():
    provider, fake_client = _provider([_text_response('{"text": "ok"}')])

    await _drain(provider.generate_stream_with_schema(
        "sys", [], {"text": "t"}, tools=[_SELECT_SPEC, _TICKETS_SPEC], required_tools=[_SELECT_SPEC],
    ))

    sent = fake_client.chat.completions.calls[0]
    assert sent["tools"] == [{
        "type": "function",
        "function": {
            "name": "source_flights_select", "description": "Grep over the flights archive.",
            "parameters": _SELECT_SPEC.parameters,
        },
    }]
    assert sent["tool_choice"] == "required"


async def test_no_required_tools_sends_the_full_catalog_with_no_tool_choice():
    provider, fake_client = _provider([_text_response('{"text": "ok"}')])

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC, _TICKETS_SPEC]))

    sent = fake_client.chat.completions.calls[0]
    assert {t["function"]["name"] for t in sent["tools"]} == {"source_flights_select", "source_tickets_select"}
    assert "tool_choice" not in sent


async def test_first_round_of_a_forced_turn_restricts_tool_choice_then_round_two_is_auto():
    provider, fake_client = _provider([
        _tool_call_response("call_1", "source_flights_select", '{"value": "paris"}'),
        _text_response('{"text": "Paris it is."}'),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[_SELECT_SPEC])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        force_required_tools=True,
    ):
        pass

    round_1, round_2 = fake_client.chat.completions.calls
    assert round_1["tool_choice"] == "required"
    assert "tool_choice" not in round_2


async def test_force_required_tools_false_never_restricts_even_with_a_must_source():
    """The second turn in the same state (TrackingProcessor.
    force_required_tools_for returns False) — auto from round 1, even
    though this state does declare an ai-must-query-sources tool."""
    provider, fake_client = _provider([
        _tool_call_response("call_1", "source_flights_select", '{"value": "paris"}'),
        _text_response('{"text": "Paris it is."}'),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[_SELECT_SPEC])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        pass

    assert "tool_choice" not in fake_client.chat.completions.calls[0]


async def test_a_state_with_only_ai_may_query_sources_is_never_forced():
    provider, fake_client = _provider([_text_response('{"text": "hi"}')])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=[], required_specs=[])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        force_required_tools=True,
    ):
        pass

    assert "tool_choice" not in fake_client.chat.completions.calls[0]
