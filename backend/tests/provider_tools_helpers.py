"""Per-SDK harnesses for the native tool-calling tests: each one stubs its
provider's async client, builds that SDK's own response shapes, and
inspects what the provider actually sent — so test_provider_tools.py can
state every provider-neutral expectation once, parametrized over the
three providers.
"""
from __future__ import annotations

import json

from google.genai import types

from ai._providers.anthropic_provider_v2 import AnthropicProvider
from ai._providers.gemini_provider_v2 import GeminiProvider
from ai._providers.openai_provider_v2 import OpenAICompatibleProvider
from ai.llm_provider import AIServiceConfig, ToolSpec

SELECT_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)

TICKETS_SPEC = ToolSpec(
    name="source_tickets_select",
    description="Grep over the tickets archive.",
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)


class FakeToolSet:
    """Enough of tracking.sources.ToolSet's own contract (specs()/call()/
    required_specs()/session_id) for AiService's loop to drive — call()
    never raises, matching the real one's contract, and just returns
    whatever's queued next."""

    def __init__(self, specs: list[ToolSpec], results: list[str], required_specs: list[ToolSpec] | None = None) -> None:
        self._specs = specs
        self._results = list(results)
        self._required_specs = required_specs or []
        self.calls: list[tuple[str, dict]] = []
        self.session_id = 99

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def required_specs(self) -> list[ToolSpec]:
        return self._required_specs

    def tool_event(self, name: str, arguments: dict, phase: str, **result_fields) -> dict:
        payload = {
            "phase": phase, "name": name, "source": name, "method": None,
            "label": name, "description": None, "arguments": arguments, "round": result_fields.get("round"),
        }
        if phase == "result":
            result = result_fields.get("result", "")
            error = result.startswith("error:")
            payload.update({
                "result": result, "rows": 0 if error else max(0, len(result.splitlines()) - 1),
                "error": error, "duration_ms": result_fields.get("duration_ms"),
            })
        return payload

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self._results.pop(0)


async def drain(stream) -> str:
    out = ""
    async for chunk in stream:
        out += chunk
    return out


# --- Anthropic -------------------------------------------------------------

class AnthropicUsage:
    def __init__(
        self, input_tokens: int = 2, output_tokens: int = 1,
        cache_read_input_tokens: int | None = None, cache_creation_input_tokens: int | None = None,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class AnthropicTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class AnthropicToolUseBlock:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict) -> None:
        self.id = id
        self.name = name
        self.input = input


class AnthropicFinalMessage:
    def __init__(self, stop_reason: str, content=(), usage: AnthropicUsage | None = None) -> None:
        self.stop_reason = stop_reason
        self.content = list(content)
        self.usage = usage or AnthropicUsage()


class _AnthropicMessageStream:
    def __init__(self, text_chunks: list[str], final_message: AnthropicFinalMessage) -> None:
        self._text_chunks = text_chunks
        self._final_message = final_message

    async def __aenter__(self) -> "_AnthropicMessageStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    @property
    def text_stream(self):
        return self._iter_text()

    async def _iter_text(self):
        for chunk in self._text_chunks:
            yield chunk

    async def get_final_message(self) -> AnthropicFinalMessage:
        return self._final_message


class _AnthropicMessagesResource:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def stream(self, **kwargs) -> _AnthropicMessageStream:
        self.calls.append(kwargs)
        text_chunks, final_message = self._responses.pop(0)
        return _AnthropicMessageStream(text_chunks, final_message)


class _AnthropicAsyncClient:
    def __init__(self, responses: list) -> None:
        self.messages = _AnthropicMessagesResource(responses)


class AnthropicHarness:
    name = "anthropic"
    synthetic_tools: frozenset[str] = frozenset()

    def provider(self, responses: list) -> tuple[AnthropicProvider, _AnthropicAsyncClient]:
        provider = AnthropicProvider(AIServiceConfig("anthropic", "claude-x", "k", None, "x"))
        fake_client = _AnthropicAsyncClient(responses)
        provider._async_client_for_current_loop = lambda: fake_client  # type: ignore[method-assign]
        return provider, fake_client

    def text_response(self, json_text: str):
        return ([json_text], AnthropicFinalMessage("end_turn"))

    def tool_call_response(self, call_id: str, name: str, args: dict):
        return ([], AnthropicFinalMessage("tool_use", content=[AnthropicToolUseBlock(call_id, name, args)]))

    def calls(self, fake_client) -> list[dict]:
        return fake_client.messages.calls

    def assert_no_tools_sent(self, call: dict) -> None:
        assert "tools" not in call

    def declared_names(self, call: dict) -> set[str]:
        return {t["name"] for t in call["tools"]}

    def assert_declaration_shape(self, call: dict, spec: ToolSpec) -> None:
        assert call["tools"] == [{"name": spec.name, "description": spec.description, "input_schema": spec.parameters}]

    def forced_names(self, call: dict) -> list[str] | None:
        if call.get("tool_choice") != {"type": "any"}:
            return None
        return [t["name"] for t in call["tools"]]

    def assistant_tool_call_name(self, call: dict) -> str:
        message = call["messages"][-2]
        assert message["role"] == "assistant"
        block = message["content"][-1]
        assert block["type"] == "tool_use"
        return block["name"]

    def tool_result_content(self, call: dict) -> str:
        message = call["messages"][-1]
        assert message["role"] == "user"
        return message["content"][0]["content"]


# --- OpenAI-compatible -----------------------------------------------------

class OpenAIPromptTokensDetails:
    def __init__(self, cached_tokens: int | None = None) -> None:
        self.cached_tokens = cached_tokens


class OpenAIUsage:
    def __init__(
        self, total_tokens: int = 3, prompt_tokens: int = 2, completion_tokens: int = 1,
        prompt_tokens_details: OpenAIPromptTokensDetails | None = None,
    ) -> None:
        self.total_tokens = total_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = prompt_tokens_details


class _OpenAIDeltaFunction:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _OpenAIDeltaToolCall:
    def __init__(self, index: int, id: str | None = None, name: str | None = None, arguments: str | None = None) -> None:
        self.index = index
        self.id = id
        self.function = _OpenAIDeltaFunction(name=name, arguments=arguments)


class _OpenAIDelta:
    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _OpenAIChoice:
    def __init__(self, delta: _OpenAIDelta, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class OpenAIChunk:
    def __init__(self, choices: list | None = None, usage: OpenAIUsage | None = None) -> None:
        self.choices = choices or []
        self.usage = usage


class _OpenAIStream:
    def __init__(self, chunks: list[OpenAIChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _OpenAICompletions:
    def __init__(self, responses: list[list[OpenAIChunk]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _OpenAIStream(self._responses.pop(0))


class _OpenAIAsyncClient:
    def __init__(self, responses: list[list[OpenAIChunk]]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = _OpenAICompletions(responses)


class OpenAIHarness:
    name = "openai"
    synthetic_tools: frozenset[str] = frozenset()

    def provider(self, responses: list) -> tuple[OpenAICompatibleProvider, _OpenAIAsyncClient]:
        provider = OpenAICompatibleProvider(AIServiceConfig("openai", "gpt-x", "k", None, "x"))
        fake_client = _OpenAIAsyncClient(responses)
        provider._client = fake_client  # type: ignore[assignment]
        return provider, fake_client

    def text_response(self, json_text: str, finish_reason: str = "stop") -> list[OpenAIChunk]:
        return [
            OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(content=json_text))]),
            OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(), finish_reason=finish_reason)], usage=OpenAIUsage()),
        ]

    def tool_call_response(self, call_id: str, name: str, args: dict) -> list[OpenAIChunk]:
        arguments_json = json.dumps(args)
        midpoint = len(arguments_json) // 2
        return [
            OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(tool_calls=[_OpenAIDeltaToolCall(0, id=call_id, name=name)]))]),
            OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(tool_calls=[_OpenAIDeltaToolCall(0, arguments=arguments_json[:midpoint])]))]),
            OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(tool_calls=[_OpenAIDeltaToolCall(0, arguments=arguments_json[midpoint:])]))]),
            OpenAIChunk(choices=[_OpenAIChoice(_OpenAIDelta(), finish_reason="tool_calls")], usage=OpenAIUsage()),
        ]

    def calls(self, fake_client) -> list[dict]:
        return fake_client.chat.completions.calls

    def assert_no_tools_sent(self, call: dict) -> None:
        assert "tools" not in call

    def declared_names(self, call: dict) -> set[str]:
        return {t["function"]["name"] for t in call["tools"]}

    def assert_declaration_shape(self, call: dict, spec: ToolSpec) -> None:
        assert call["tools"] == [{
            "type": "function",
            "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
        }]

    def forced_names(self, call: dict) -> list[str] | None:
        if call.get("tool_choice") != "required":
            return None
        return [t["function"]["name"] for t in call["tools"]]

    def assistant_tool_call_name(self, call: dict) -> str:
        message = call["messages"][-2]
        assert message["role"] == "assistant"
        return message["tool_calls"][0]["function"]["name"]

    def tool_result_content(self, call: dict) -> str:
        message = call["messages"][-1]
        assert message["role"] == "tool"
        return message["content"]


# --- Gemini ----------------------------------------------------------------

class GeminiUsage:
    def __init__(
        self, total_token_count: int = 3, prompt_token_count: int = 2, candidates_token_count: int = 1,
        cached_content_token_count: int | None = None,
    ) -> None:
        self.total_token_count = total_token_count
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count
        self.cached_content_token_count = cached_content_token_count


class GeminiFunctionCall:
    def __init__(self, name: str, args: dict, id: str | None = None) -> None:
        self.name = name
        self.args = args
        self.id = id


class GeminiPart:
    def __init__(self, function_call: GeminiFunctionCall | None = None, thought_signature: bytes | None = None, text: str = "") -> None:
        self.function_call = function_call
        self.thought_signature = thought_signature
        self.text = text


class GeminiContent:
    def __init__(self, parts: list[GeminiPart]) -> None:
        self.parts = parts


class GeminiCandidate:
    def __init__(self, content: GeminiContent | None = None, finish_reason=None) -> None:
        self.content = content
        self.finish_reason = finish_reason


class GeminiChunk:
    def __init__(self, candidates: list[GeminiCandidate] | None = None, usage_metadata: GeminiUsage | None = None, text: str = "") -> None:
        self.candidates = candidates or []
        self.usage_metadata = usage_metadata
        self._text = text

    @property
    def text(self) -> str:
        return self._text


class _GeminiStream:
    def __init__(self, chunks: list[GeminiChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _GeminiModels:
    def __init__(self, responses: list[list[GeminiChunk]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        return _GeminiStream(self._responses.pop(0))


class _GeminiClient:
    def __init__(self, responses: list[list[GeminiChunk]]) -> None:
        self.aio = type("Aio", (), {})()
        self.aio.models = _GeminiModels(responses)


class GeminiHarness:
    name = "gemini"
    synthetic_tools: frozenset[str] = frozenset({"respond"})

    def provider(self, responses: list) -> tuple[GeminiProvider, _GeminiClient]:
        provider = GeminiProvider(AIServiceConfig("gemini", "gemini-x", "k", None, "x"))
        fake_client = _GeminiClient(responses)
        provider._GeminiProvider__client = lambda: fake_client  # type: ignore[attr-defined]
        return provider, fake_client

    def text_response(self, json_text: str) -> list[GeminiChunk]:
        return [GeminiChunk(candidates=[GeminiCandidate(finish_reason=types.FinishReason.STOP)], usage_metadata=GeminiUsage(), text=json_text)]

    def function_call_response(
        self, name: str, args: dict, call_id: str | None = None, thought_signature: bytes | None = None,
    ) -> list[GeminiChunk]:
        part = GeminiPart(function_call=GeminiFunctionCall(name=name, args=args, id=call_id), thought_signature=thought_signature)
        return [GeminiChunk(
            candidates=[GeminiCandidate(content=GeminiContent(parts=[part]), finish_reason=types.FinishReason.STOP)],
            usage_metadata=GeminiUsage(),
        )]

    def tool_call_response(self, call_id: str, name: str, args: dict) -> list[GeminiChunk]:
        return self.function_call_response(name, args, call_id=call_id)

    def calls(self, fake_client) -> list[dict]:
        return fake_client.aio.models.calls

    def assert_no_tools_sent(self, call: dict) -> None:
        assert call["config"].tools is None
        assert call["config"].response_schema is not None

    def declared_names(self, call: dict) -> set[str]:
        return {decl.name for tool in call["config"].tools for decl in tool.function_declarations}

    def assert_declaration_shape(self, call: dict, spec: ToolSpec) -> None:
        assert self.declared_names(call) == {spec.name} | self.synthetic_tools
        assert call["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY

    def forced_names(self, call: dict) -> list[str] | None:
        return call["config"].tool_config.function_calling_config.allowed_function_names or None

    def assistant_tool_call_name(self, call: dict) -> str:
        content = call["contents"][-2]
        assert content.role == "model"
        return content.parts[-1].function_call.name

    def tool_result_content(self, call: dict) -> str:
        content = call["contents"][-1]
        assert content.role == "user"
        return content.parts[0].function_response.response["result"]


HARNESSES = [AnthropicHarness(), OpenAIHarness(), GeminiHarness()]
