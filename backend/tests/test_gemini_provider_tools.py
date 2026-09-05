"""Native tool-calling — Gemini-side translation (ToolSpec -> a Gemini
FunctionDeclaration, ToolCallsRequested when the model calls a real
declared tool, the two neutral tool history shapes <-> Gemini's own
functionCall/functionResponse parts) and the "respond as a tool"
fallback this provider needs since response_schema and tools don't
reliably combine here: a synthetic "respond" tool, forced via
tool_config, whose own arguments *are* the structured answer. All with
the Gemini SDK's async client stubbed, never a real network call.
"""
from __future__ import annotations

import pytest
from google.genai import types

from ai.ai_service import AiService, MAX_TOOL_ROUNDS, _TOOL_ERROR_DIRECTIVE
from ai._providers.gemini_provider_v2 import GeminiProvider
from ai.llm_provider import AIServiceConfig, SystemPrompt, ToolCall, ToolCallsRequested, ToolSpec

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
        self, total_token_count: int = 3, prompt_token_count: int = 2, candidates_token_count: int = 1,
        cached_content_token_count: int | None = None,
    ) -> None:
        self.total_token_count = total_token_count
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count
        # None (the default) matches a real UsageMetadata that never
        # populated this — the provider must fall back to 0.
        self.cached_content_token_count = cached_content_token_count


class _FakeFunctionCall:
    def __init__(self, name: str, args: dict, id: str | None = None) -> None:
        self.name = name
        self.args = args
        self.id = id


class _FakePart:
    def __init__(self, function_call: _FakeFunctionCall | None = None, thought_signature: bytes | None = None, text: str = "") -> None:
        self.function_call = function_call
        self.thought_signature = thought_signature
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeCandidate:
    def __init__(self, content: _FakeContent | None = None, finish_reason=None) -> None:
        self.content = content
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, candidates: list[_FakeCandidate] | None = None, usage_metadata: _FakeUsage | None = None, text: str = "") -> None:
        self.candidates = candidates or []
        self.usage_metadata = usage_metadata
        self._text = text

    @property
    def text(self) -> str:
        return self._text


class _FakeStream:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _FakeModels:
    def __init__(self, responses: list[list[_FakeChunk]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self._responses.pop(0))


class _FakeGeminiClient:
    def __init__(self, responses: list[list[_FakeChunk]]) -> None:
        self.aio = type("Aio", (), {})()
        self.aio.models = _FakeModels(responses)


class _FakeToolSet:
    """Enough of tracking.sources.ToolSet's own contract (specs()/call()/
    required_specs()/summary_text()/session_id) for AiService's loop to
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

    def status_text(self, name: str) -> str:
        return f"Searching {name}…"

    def summary_text(self, name: str, arguments: dict, result: str) -> str:
        return f"Searched {name} · fake summary"

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self._results.pop(0)


def _provider(responses: list[list[_FakeChunk]]) -> tuple[GeminiProvider, _FakeGeminiClient]:
    provider = GeminiProvider(AIServiceConfig("gemini", "gemini-x", "k", None, "x"))
    fake_client = _FakeGeminiClient(responses)
    provider._GeminiProvider__client = lambda: fake_client  # type: ignore[attr-defined]
    return provider, fake_client


def _text_response(json_text: str) -> list[_FakeChunk]:
    return [_FakeChunk(candidates=[_FakeCandidate(finish_reason=types.FinishReason.STOP)], usage_metadata=_FakeUsage(), text=json_text)]


def _function_call_response(
    name: str, args: dict, call_id: str | None = None, thought_signature: bytes | None = None,
) -> list[_FakeChunk]:
    part = _FakePart(function_call=_FakeFunctionCall(name=name, args=args, id=call_id), thought_signature=thought_signature)
    return [_FakeChunk(
        candidates=[_FakeCandidate(content=_FakeContent(parts=[part]), finish_reason=types.FinishReason.STOP)],
        usage_metadata=_FakeUsage(),
    )]


async def _drain(stream) -> str:
    out = ""
    async for chunk in stream:
        out += chunk
    return out


# (a) a response with no tools involved streams normally, and the
# request never even carries `tools`/`tool_config` — a state with no
# `tools:` must produce the exact same request as before tool-calling
# existed (still response_schema-based, never the "respond" fallback).
async def test_no_tools_streams_normally_and_never_sends_tools_config():
    provider, fake_client = _provider([_text_response('{"text": "hi"}')])

    out = await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))

    assert out == '{"text": "hi"}'
    config = fake_client.aio.models.calls[0]["config"]
    assert config.tools is None
    assert config.response_schema is not None


async def test_a_real_tool_call_raises_ToolCallsRequested():
    provider, fake_client = _provider([_function_call_response("source_flights_select", {"value": "paris"}, call_id="call_1")])

    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]):
            pass

    assert exc_info.value.calls == [ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"})]
    config = fake_client.aio.models.calls[0]["config"]
    declared_names = {decl.name for tool in config.tools for decl in tool.function_declarations}
    assert declared_names == {"respond", "source_flights_select"}
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY


async def test_a_real_tool_call_with_no_id_gets_one_generated():
    provider, _ = _provider([_function_call_response("source_flights_select", {"value": "paris"}, call_id=None)])

    with pytest.raises(ToolCallsRequested) as exc_info:
        async for _ in provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]):
            pass

    assert exc_info.value.calls[0].id  # non-empty, generated


# (b) one tool call, then the real response — for Gemini, the "real
# response" is itself a call to the synthetic "respond" tool.
async def test_ai_service_resolves_one_tool_call_then_streams_the_final_response():
    provider, fake_client = _provider([
        _function_call_response("source_flights_select", {"value": "paris"}, call_id="call_1"),
        _function_call_response("respond", {"text": "Paris it is."}),
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
    assert len(fake_client.aio.models.calls) == 2
    second_round_contents = fake_client.aio.models.calls[1]["contents"]
    assert second_round_contents[-2].role == "model"
    assert second_round_contents[-2].parts[-1].function_call.name == "source_flights_select"
    assert second_round_contents[-1].role == "user"
    assert second_round_contents[-1].parts[0].function_response.name == "source_flights_select"
    assert second_round_contents[-1].parts[0].function_response.response == {"result": "city,country\nParis,France\n"}


# (c) two rounds before the final "respond" call.
async def test_ai_service_resolves_two_tool_call_rounds_before_the_final_response():
    provider, fake_client = _provider([
        _function_call_response("source_flights_select", {"value": "paris"}, call_id="call_1"),
        _function_call_response("source_flights_select", {"value": "berlin"}, call_id="call_2"),
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
    assert len(fake_client.aio.models.calls) == 3


# (d) exceeding MAX_TOOL_ROUNDS: tools are dropped for one final round so
# the model must answer instead of the turn raising.
async def test_exceeding_max_tool_rounds_forces_a_final_answer_with_tools_disabled():
    provider, fake_client = _provider([
        *(
            _function_call_response("source_flights_select", {"value": "x"}, call_id=f"call_{i}")
            for i in range(MAX_TOOL_ROUNDS)
        ),
        _text_response('{"text": "Best I can tell you without more lookups."}'),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["row"] * MAX_TOOL_ROUNDS)

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Best I can tell you without more lookups."
    assert len(fake_client.aio.models.calls) == MAX_TOOL_ROUNDS + 1
    assert len(tool_set.calls) == MAX_TOOL_ROUNDS


# (e) ToolSet.call itself never raises (its own contract — see
# test_tool_set.py) but can report a failure as an "error: ..." string;
# AiService's loop treats that exactly like any other tool result and
# the turn still completes normally.
async def test_a_failed_tool_lookup_feeds_an_error_string_back_and_the_turn_still_completes():
    provider, fake_client = _provider([
        _function_call_response("source_flights_select", {"value": "paris"}, call_id="call_1"),
        _function_call_response("respond", {"text": "Sorry, lookup failed."}),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["error: unknown tool 'source_flights_select'."])

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "Sorry, lookup failed."
    second_round_contents = fake_client.aio.models.calls[1]["contents"]
    assert second_round_contents[-1].parts[0].function_response.response == {
        "result": "error: unknown tool 'source_flights_select'." + _TOOL_ERROR_DIRECTIVE,
    }


# (f) round-trip: the neutral tool history shapes translate to/from
# Gemini's own wire format exactly.
def test_build_contents_round_trips_the_neutral_tool_history_shapes():
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

    contents = provider._GeminiProvider__build_contents(history)  # type: ignore[attr-defined]

    assert len(contents) == 3
    assert contents[0].role == "user" and contents[0].parts[0].text == "where's my flight?"
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "Checking..."
    assert contents[1].parts[1].function_call.name == "source_flights_select"
    assert contents[1].parts[1].function_call.args == {"value": "paris"}
    assert contents[2].role == "user"
    assert contents[2].parts[0].function_response.name == "source_flights_select"
    assert contents[2].parts[0].function_response.response == {"result": "city,country\nParis,France\n"}


def test_build_contents_groups_two_tool_results_from_the_same_round_into_one_content():
    provider, _ = _provider([])
    history = [
        {
            "role": "assistant",
            "tool_calls": [
                ToolCall(id="call_1", name="source_flights_select", arguments={"value": "paris"}),
                ToolCall(id="call_2", name="source_flights_select", arguments={"value": "berlin"}),
            ],
            "content": None,
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "paris row"},
        {"role": "tool", "tool_call_id": "call_2", "content": "berlin row"},
    ]

    contents = provider._GeminiProvider__build_contents(history)  # type: ignore[attr-defined]

    assert len(contents) == 2  # the assistant turn, then ONE user turn holding both results
    assert contents[1].role == "user"
    assert len(contents[1].parts) == 2
    assert contents[1].parts[0].function_response.response == {"result": "paris row"}
    assert contents[1].parts[1].function_response.response == {"result": "berlin row"}


# (g) the "respond" fallback produces the same JSON shape schema mode
# does — proven by running it through AiService's own real parser, which
# must extract "text"/"env" exactly as it would from a genuine
# response_schema-based reply.
async def test_respond_fallback_is_parsed_by_ai_service_exactly_like_a_schema_response():
    provider, _ = _provider([_function_call_response("respond", {"text": "hi there", "env": "k: v"})])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=[])
    reported: dict[str, str] = {}

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: reported.__setitem__(k, v),
            schema={"text": "the reply", "env": "context"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "hi there"
    assert reported.get("env") == "k: v"


def test_respond_tool_declaration_parameters_match_the_schema_fields():
    provider, _ = _provider([])
    declaration = provider._GeminiProvider__respond_tool_declaration({"text": "the reply", "env": "context"})  # type: ignore[attr-defined]

    assert declaration.name == "respond"
    # pydantic coerces the plain dict passed in into a real types.Schema —
    # attribute access, not dict subscripting.
    assert set(declaration.parameters.properties.keys()) == {"text", "env"}
    assert declaration.parameters.required == ["text", "env"]


# Gemini stamps every functionCall part with an opaque thought_signature
# and rejects the next request (400 INVALID_ARGUMENT "Function call is
# missing a thought_signature in functionCall parts") unless that exact
# part comes back in the model turn preceding the functionResponse. A
# part rebuilt from the neutral ToolCall has no signature — so the
# provider must hand its own parts back through assistant_content and
# replay them verbatim. https://ai.google.dev/gemini-api/docs/thought-signatures
async def test_a_tool_call_keeps_its_thought_signature_through_the_replayed_history():
    provider, _ = _provider([_function_call_response(
        "source_flights_select", {"value": "VY3003"}, call_id="c1", thought_signature=b"opaque-sig",
    )])

    with pytest.raises(ToolCallsRequested) as raised:
        await _drain(provider.generate_stream_with_schema("sys", [{"role": "user", "content": "hi"}], {"text": "t"}, tools=[_SELECT_SPEC]))

    requested = raised.value
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": requested.calls, "content": requested.assistant_content},
        {"role": "tool", "tool_call_id": requested.calls[0].id, "content": "row"},
    ]
    contents = provider._GeminiProvider__build_contents(history)  # type: ignore[attr-defined]

    model_turn = contents[1]
    assert model_turn.role == "model"
    assert model_turn.parts[0].function_call.name == "source_flights_select"
    assert model_turn.parts[0].function_call.args == {"value": "VY3003"}
    assert model_turn.parts[0].thought_signature == b"opaque-sig"
    assert contents[2].parts[0].function_response.name == "source_flights_select"


def test_a_tool_call_asked_by_another_provider_is_rebuilt_without_a_signature():
    """Cascade failover mid-loop: the assistant turn carries no Gemini
    replay payload (text or None) — rebuilt from the neutral ToolCall,
    exactly as before."""
    provider, _ = _provider([])
    history = [
        {"role": "assistant", "tool_calls": [ToolCall(id="x", name="source_flights_select", arguments={"value": "a"})], "content": None},
        {"role": "tool", "tool_call_id": "x", "content": "row"},
    ]
    contents = provider._GeminiProvider__build_contents(history)  # type: ignore[attr-defined]
    assert contents[0].parts[0].function_call.name == "source_flights_select"
    assert contents[0].parts[0].thought_signature is None


async def test_a_signature_streamed_on_its_own_chunk_lands_on_the_function_call_part():
    """Streaming can deliver the thought_signature on a chunk whose part
    carries nothing else, before or after the functionCall part — it
    must end up on the functionCall part, the only place Gemini accepts it."""
    chunks = [
        _FakeChunk(candidates=[_FakeCandidate(content=_FakeContent(parts=[_FakePart(thought_signature=b"sig-alone")]))]),
        _FakeChunk(candidates=[_FakeCandidate(
            content=_FakeContent(parts=[_FakePart(function_call=_FakeFunctionCall(name="source_flights_select", args={"value": "VY1"}, id="c1"))]),
            finish_reason=types.FinishReason.STOP,
        )], usage_metadata=_FakeUsage()),
    ]
    provider, _ = _provider([chunks])

    with pytest.raises(ToolCallsRequested) as raised:
        await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]))

    parts = raised.value.assistant_content["gemini_parts"]
    assert len(parts) == 1
    assert parts[0].function_call.name == "source_flights_select"
    assert parts[0].thought_signature == b"sig-alone"


async def test_text_streamed_alongside_the_call_is_replayed_in_order():
    chunks = [
        _FakeChunk(candidates=[_FakeCandidate(content=_FakeContent(parts=[_FakePart(text="Let me ")]))]),
        _FakeChunk(candidates=[_FakeCandidate(content=_FakeContent(parts=[_FakePart(text="check.")]))]),
        _FakeChunk(candidates=[_FakeCandidate(
            content=_FakeContent(parts=[_FakePart(function_call=_FakeFunctionCall(name="source_flights_select", args={"value": "VY1"}), thought_signature=b"s")]),
            finish_reason=types.FinishReason.STOP,
        )], usage_metadata=_FakeUsage()),
    ]
    provider, _ = _provider([chunks])

    with pytest.raises(ToolCallsRequested) as raised:
        await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC]))

    parts = raised.value.assistant_content["gemini_parts"]
    assert [p.text for p in parts] == ["Let me check.", None]
    assert parts[1].function_call.name == "source_flights_select" and parts[1].thought_signature == b"s"


# ai-must-read-sources forcing — see AiService.generate_stream_with_
# metadata's own force_required_tools/required_tools and
# TrackingProcessor.force_required_tools_for, which decides it. The
# provider itself only ever translates "required_tools was given" into
# its own function_calling_config dialect; it never decides *whether* to
# force. Unlike Anthropic/OpenAI, the full `tools` declaration (respond
# included) is always sent — allowed_function_names is what restricts the
# *callable* set for this one round, deliberately excluding "respond".
async def test_required_tools_restricts_allowed_function_names_excluding_respond():
    provider, fake_client = _provider([_text_response('{"text": "ok"}')])

    await _drain(provider.generate_stream_with_schema(
        "sys", [], {"text": "t"}, tools=[_SELECT_SPEC, _TICKETS_SPEC], required_tools=[_SELECT_SPEC],
    ))

    config = fake_client.aio.models.calls[0]["config"]
    declared_names = {decl.name for tool in config.tools for decl in tool.function_declarations}
    assert declared_names == {"respond", "source_flights_select", "source_tickets_select"}
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY
    assert config.tool_config.function_calling_config.allowed_function_names == ["source_flights_select"]


async def test_no_required_tools_leaves_allowed_function_names_unset():
    provider, fake_client = _provider([_text_response('{"text": "ok"}')])

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, tools=[_SELECT_SPEC, _TICKETS_SPEC]))

    config = fake_client.aio.models.calls[0]["config"]
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY
    assert not config.tool_config.function_calling_config.allowed_function_names


async def test_first_round_of_a_forced_turn_restricts_allowed_names_then_round_two_is_auto():
    provider, fake_client = _provider([
        _function_call_response("source_flights_select", {"value": "paris"}, call_id="call_1"),
        _function_call_response("respond", {"text": "Paris it is."}),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[_SELECT_SPEC])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        force_required_tools=True,
    ):
        pass

    round_1, round_2 = fake_client.aio.models.calls
    assert round_1["config"].tool_config.function_calling_config.allowed_function_names == ["source_flights_select"]
    assert not round_2["config"].tool_config.function_calling_config.allowed_function_names


async def test_force_required_tools_false_never_restricts_even_with_a_must_source():
    """The second turn in the same state (TrackingProcessor.
    force_required_tools_for returns False) — auto from round 1, even
    though this state does declare an ai-must-read-sources tool."""
    provider, fake_client = _provider([
        _function_call_response("source_flights_select", {"value": "paris"}, call_id="call_1"),
        _function_call_response("respond", {"text": "Paris it is."}),
    ])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=["city,country\nParis,France\n"], required_specs=[_SELECT_SPEC])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        pass

    assert not fake_client.aio.models.calls[0]["config"].tool_config.function_calling_config.allowed_function_names


async def test_a_state_with_only_ai_may_read_sources_is_never_forced():
    provider, fake_client = _provider([_function_call_response("respond", {"text": "hi"})])
    ai_service = AiService(provider)
    tool_set = _FakeToolSet([_SELECT_SPEC], results=[], required_specs=[])

    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        force_required_tools=True,
    ):
        pass

    assert not fake_client.aio.models.calls[0]["config"].tool_config.function_calling_config.allowed_function_names


# (h) SystemPrompt — no native cache breakpoint here (unlike Anthropic),
# so this provider just concatenates stable+volatile into one
# system_instruction; see ai.llm_provider.SystemPrompt.
async def test_a_plain_str_system_prompt_is_sent_as_is():
    provider, fake_client = _provider([_text_response('{"text": "hi"}')])

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}))

    assert fake_client.aio.models.calls[0]["config"].system_instruction == "sys"


async def test_a_system_prompt_is_sent_as_stable_then_volatile_concatenated():
    provider, fake_client = _provider([_text_response('{"text": "hi"}')])

    await _drain(provider.generate_stream_with_schema(
        SystemPrompt(stable="stable part", volatile="volatile part"), [], {"text": "t"},
    ))

    assert fake_client.aio.models.calls[0]["config"].system_instruction == "stable partvolatile part"


# (i) cache-read accounting — already folded into prompt_token_count, so
# input_tokens is untouched; cache_creation_tokens is always 0 (Gemini has
# no cache-write concept of its own).
async def test_cache_read_tokens_are_reported_and_input_tokens_is_untouched():
    responses = [_FakeChunk(
        candidates=[_FakeCandidate(finish_reason=types.FinishReason.STOP)],
        usage_metadata=_FakeUsage(prompt_token_count=50, candidates_token_count=5, cached_content_token_count=40),
        text='{"text": "hi"}',
    )]
    provider, _ = _provider([responses])
    events: list[tuple[str, object]] = []

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))

    assert ("cache_read_tokens", 40) in events
    assert ("cache_creation_tokens", 0) in events
    assert ("input_tokens", 50) in events


async def test_a_usage_with_no_cache_field_reports_zero_cache_read():
    provider, fake_client = _provider([_text_response('{"text": "hi"}')])
    events: list[tuple[str, object]] = []

    await _drain(provider.generate_stream_with_schema("sys", [], {"text": "t"}, on_metadata=lambda k, v: events.append((k, v))))

    assert ("cache_read_tokens", 0) in events
    assert ("cache_creation_tokens", 0) in events
