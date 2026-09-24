from __future__ import annotations

import asyncio
import json
import threading
import uuid
from contextlib import contextmanager
from typing import Any, AsyncIterator, Generator

from google import genai
from google.genai import types
from google.genai.errors import APIError

from system.cascade import OnRetry
from ai.response_schema import Field, ObjectField
from system.logging_factory import LoggerFactory
from ai.llm_provider import (
	AIServiceConfig,
	AIServiceError,
	AIServiceProviderOutputTruncatedError,
	AIServiceProviderPermanentError,
	AIServiceProviderRateLimitedError,
	AIServiceProviderUnavailableError,
	AIServiceRequestError,
	LLMProvider,
	MetadataCallback,
	SystemPrompt,
	ToolCall,
	ToolCallsRequested,
	ToolSpec,
	content_to_text,
	is_text_fragments,
	with_opening_turn,
)

logger = LoggerFactory.get_logger(__name__)
REQUEST_TIMEOUT_MS: int = 30_000
_REPLAY_PARTS_KEY = "gemini_parts"
_RESPOND_TOOL_NAME = "respond"


def _copy_model_part(part: Any) -> types.Part:
	"""A real types.Part rebuilt from one streamed part of the model's
	turn — text, functionCall and, above all, its `thought_signature`
	(also kept on a part that carries nothing else: in streaming Gemini
	can deliver the signature on its own chunk, see _consolidate_model_parts)."""
	function_call = getattr(part, "function_call", None)
	return types.Part(
		text=getattr(part, "text", None) or None,
		function_call=types.FunctionCall(
			id=getattr(function_call, "id", None),
			name=function_call.name,
			args=dict(function_call.args or {}),
		) if function_call is not None else None,
		thought=getattr(part, "thought", None) or None,
		thought_signature=getattr(part, "thought_signature", None),
	)


def _consolidate_model_parts(parts: list[types.Part]) -> list[types.Part]:
	"""The model turn to replay, in the order it streamed: consecutive
	text-only parts merged, and a signature that streamed on a part of
	its own (no text, no call) moved onto the first functionCall part
	still lacking one — Gemini requires the signature *on* the functionCall
	part it signed, and rejects a bare functionCall part without it."""
	merged: list[types.Part] = []
	orphan_signatures: list[bytes] = []
	for part in parts:
		if part.function_call is None and not part.text:
			if part.thought_signature:
				orphan_signatures.append(part.thought_signature)
			continue
		if part.function_call is None and merged and merged[-1].function_call is None \
				and not part.thought_signature and not merged[-1].thought_signature:
			merged[-1].text = (merged[-1].text or "") + part.text
			continue
		merged.append(part)
	for part in merged:
		if not orphan_signatures:
			break
		if part.function_call is not None and not part.thought_signature:
			part.thought_signature = orphan_signatures.pop(0)
	return merged


@contextmanager
def _handle_gemini_errors() -> Generator[None, None, None]:
	"""Context manager centralizzato per la gestione e rimappatura delle eccezioni dell'SDK Google GenAI."""
	try:
		yield
	except APIError as exc:
		code: int | None = getattr(exc, "code", None)
		message: str = getattr(exc, "message", str(exc))

		if code == 429:
			raise AIServiceProviderRateLimitedError(
				f"The Gemini API rate limit was exceeded (status 429): {message}"
			) from exc
		if code in (503, 504):
			raise AIServiceProviderUnavailableError(
				f"The Gemini service is temporarily overloaded (status {code}): {message}"
			) from exc
		if code == 400:
			raise AIServiceRequestError(
				f"Error from the Gemini API (status {code}): {message}"
			) from exc
		raise AIServiceProviderPermanentError(
			f"Error from the Gemini API (status {code}): {message}"
		) from exc
	except Exception as exc:
		raise AIServiceError(f"Unexpected error from the Gemini API: {exc}") from exc


class GeminiProvider(LLMProvider):
	def __init__(self, config: AIServiceConfig) -> None:
		super().__init__()
		self.__api_key: str = config.key
		self.__base_url: str | None = config.url
		self.__model_name: str = config.model
		self.__max_output_tokens: int = config.max_output_tokens
		self.__clients: dict[asyncio.AbstractEventLoop, genai.Client] = {}
		self.__clients_lock = threading.Lock()
		self.__sync_client: genai.Client = self.__new_client()

	def __new_client(self) -> genai.Client:
		http_options: dict[str, Any] = {"timeout": REQUEST_TIMEOUT_MS}
		if self.__base_url:
			http_options["base_url"] = self.__base_url
		return genai.Client(api_key=self.__api_key, http_options=http_options)

	def __client(self) -> genai.Client:
		loop = asyncio.get_running_loop()
		client = self.__clients.get(loop)
		if client is None:
			with self.__clients_lock:
				client = self.__clients.get(loop)
				if client is None:
					self.__prune_closed_loops()
					client = self.__new_client()
					self.__clients[loop] = client
		return client

	def __prune_closed_loops(self) -> None:
		"""Called under self.__clients_lock, right before adding a new
		entry — the one point every stale entry is guaranteed to have
		already run its last request, so dropping it here can never race
		a Client still in use. A closed loop can never run again (each
		PromptContext._run_sync call gets its own, via asyncio.run()), so
		this is pure cleanup, never a false eviction of a live loop."""
		for stale_loop in [loop for loop in self.__clients if loop.is_closed()]:
			del self.__clients[stale_loop]

	def build_schema(self, schema: dict[str, Field]) -> dict:
		return self.__json_schema_to_gemini(ObjectField(schema).json_schema())

	def __build_contents(self, history: list[dict[str, Any]]) -> list[types.Content]:
		"""Two more provider-neutral message shapes beyond plain
		{role, content} — see LLMProvider.stream_json's own
		docstring: an assistant turn that asked for tools (translated to a
		"model" Content whose parts are its own text, if any, plus one
		functionCall part per call) and a tool's own result (a "user"
		Content holding a functionResponse part — Gemini has no separate
		"tool" role). Gemini matches a functionResponse to its own call by
		name, not id (its FunctionCall/FunctionResponse have no id-based
		linking the way Anthropic/OpenAI do), so `call_name_by_id` tracks
		each call's own name only long enough to label the result that
		follows it. Every functionResponse for one round lands in the same
		Content (appended to the round's own, not a fresh Content each
		time) — the shape Gemini's own API expects for parallel calls."""
		contents: list[types.Content] = []
		call_name_by_id: dict[str, str] = {}

		def _is_function_response_round(content: types.Content) -> bool:
			return content.role == "user" and bool(content.parts) and all(
				part.function_response is not None for part in content.parts
			)

		for message in history:
			role: str = message["role"]

			if role == "assistant" and message.get("tool_calls"):
				for call in message["tool_calls"]:
					call_name_by_id[call.id] = call.name
				content = message.get("content")
				replay = content.get(_REPLAY_PARTS_KEY) if isinstance(content, dict) else None
				if replay:
					contents.append(types.Content(role="model", parts=list(replay)))
					continue
				parts: list[types.Part] = []
				if content and not isinstance(content, dict):
					parts.append(types.Part.from_text(text=str(content)))
				for call in message["tool_calls"]:
					parts.append(types.Part.from_function_call(name=call.name, args=call.arguments))
				contents.append(types.Content(role="model", parts=parts))
				continue

			if role == "tool":
				name = call_name_by_id.get(message["tool_call_id"], "")
				part = types.Part.from_function_response(name=name, response={"result": message["content"]})
				if contents and _is_function_response_round(contents[-1]):
					contents[-1].parts.append(part)
				else:
					contents.append(types.Content(role="user", parts=[part]))
				continue

			if role not in ("user", "assistant"):
				continue

			gemini_role = "model" if role == "assistant" else "user"
			content = message["content"]
			if is_text_fragments(content):
				contents.append(types.Content(
					role=gemini_role, parts=[types.Part.from_text(text=fragment) for fragment in content],
				))
				continue
			text_content: str = content_to_text(content, "Gemini")
			contents.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=text_content)]))

		return contents

	@classmethod
	def __schema_to_gemini_parameters(cls, parameters: dict) -> dict:
		"""ToolSpec.parameters is plain JSON Schema (lowercase types — see
		tracking.sources.METHOD_SCHEMAS: arrays of strings, an object of
		string fields, enums, descriptions) — Gemini's own Schema dialect
		uses uppercase type names (the same convention build_schema above
		already follows for response_schema), knows `properties`/`items`/
		`required`/`enum`/`description`, and has no `additionalProperties`/
		`minProperties`/`minItems`, which are simply dropped."""
		return cls.__json_schema_to_gemini(parameters)

	@classmethod
	def __json_schema_to_gemini(cls, schema: dict) -> dict:
		if "anyOf" in schema:
			return cls.__nullable_to_gemini(schema)
		converted: dict = {"type": str(schema.get("type", "string")).upper()}
		if "description" in schema:
			converted["description"] = schema["description"]
		if "enum" in schema:
			converted["enum"] = list(schema["enum"])
		if converted["type"] == "OBJECT":
			converted["properties"] = {
				name: cls.__json_schema_to_gemini(sub_schema) for name, sub_schema in schema.get("properties", {}).items()
			}
			if schema.get("required"):
				converted["required"] = list(schema["required"])
		if converted["type"] == "ARRAY" and isinstance(schema.get("items"), dict):
			converted["items"] = cls.__json_schema_to_gemini(schema["items"])
		return converted

	@classmethod
	def __nullable_to_gemini(cls, schema: dict) -> dict:
		inner = next(option for option in schema["anyOf"] if option.get("type") != "null")
		described = {"description": schema["description"]} if "description" in schema else {}
		return {**cls.__json_schema_to_gemini({**inner, **described}), "nullable": True}

	def __respond_tool_declaration(self, schema: dict[str, Field]) -> types.FunctionDeclaration:
		return types.FunctionDeclaration(
			name=_RESPOND_TOOL_NAME,
			description=(
				"Call this with your final structured reply once you have everything you need — "
				"its own arguments *are* the answer, one per field below."
			),
			parameters=self.build_schema(schema),
		)

	def __tool_declarations(self, tools: list[ToolSpec], schema: dict[str, Field]) -> list[types.FunctionDeclaration]:
		return [
			self.__respond_tool_declaration(schema),
			*(
				types.FunctionDeclaration(
					name=spec.name, description=spec.description,
					parameters=self.__schema_to_gemini_parameters(spec.parameters),
				)
				for spec in tools
			),
		]

	async def stream_json(
		self,
		system_prompt: "str | SystemPrompt",
		history: list[dict[str, Any]],
		schema: dict[str, Field] | None = None,
		on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None,
		tool_round: int = 1,
		required_tools: list[ToolSpec] | None = None,
	) -> AsyncIterator[str]:
		contents = self.__build_contents(with_opening_turn(history))
		schema = schema or {}
		system_instruction = SystemPrompt.coerce(system_prompt).full_text()

		if tools:
			function_calling_config = types.FunctionCallingConfig(
				mode=types.FunctionCallingConfigMode.ANY,
				**({"allowed_function_names": [spec.name for spec in required_tools]} if required_tools else {}),
			)
			config: types.GenerateContentConfig = types.GenerateContentConfig(
				system_instruction=system_instruction,
				max_output_tokens=self.__max_output_tokens,
				tools=[types.Tool(function_declarations=self.__tool_declarations(tools, schema))],
				tool_config=types.ToolConfig(function_calling_config=function_calling_config),
			)
		else:
			config = types.GenerateContentConfig(
				system_instruction=system_instruction,
				max_output_tokens=self.__max_output_tokens,
				response_mime_type="application/json",
				response_schema=self.build_schema(schema),
			)

		total_tokens = 0
		input_tokens = 0
		output_tokens = 0
		cache_read_tokens = 0
		thoughts_tokens = 0
		finish_reason: types.FinishReason | None = None
		function_call: types.FunctionCall | None = None
		replay_parts: list[types.Part] = []
		with _handle_gemini_errors():
			response_stream = await self.__client().aio.models.generate_content_stream(
				model=self.__model_name,
				contents=contents,
				config=config,
			)

			async for chunk in response_stream:
				usage = chunk.usage_metadata
				if usage is not None:
					if usage.total_token_count is not None:
						total_tokens = usage.total_token_count
					if usage.prompt_token_count is not None:
						input_tokens = usage.prompt_token_count
					if usage.candidates_token_count is not None:
						output_tokens = usage.candidates_token_count
					if getattr(usage, "cached_content_token_count", None) is not None:
						cache_read_tokens = usage.cached_content_token_count
					if getattr(usage, "thoughts_token_count", None) is not None:
						thoughts_tokens = usage.thoughts_token_count
				if chunk.candidates and chunk.candidates[0].finish_reason is not None:
					finish_reason = chunk.candidates[0].finish_reason
				if tools:
					content = chunk.candidates[0].content if chunk.candidates else None
					for part in (content.parts if content else None) or []:
						if part.function_call is not None:
							function_call = part.function_call
						replay_parts.append(_copy_model_part(part))
					if function_call is not None:
						continue
				if not chunk.text:
					continue
				yield chunk.text
		self._add_tokens(total_tokens)
		if on_metadata is not None:
			on_metadata("cache_read_tokens", cache_read_tokens)
			on_metadata("cache_creation_tokens", 0)
			on_metadata("thoughts_tokens", thoughts_tokens)
			on_metadata("input_tokens", input_tokens)
			on_metadata("output_tokens", output_tokens)
		logger.info(
			f"Gemini call finished: model={self.__model_name} finish_reason={finish_reason} "
			f"input_tokens={input_tokens} output_tokens={output_tokens} cache_read={cache_read_tokens} "
			f"thoughts_tokens={thoughts_tokens} total_tokens={total_tokens} max_output_tokens={self.__max_output_tokens}"
		)

		if tools and function_call is not None:
			if function_call.name == _RESPOND_TOOL_NAME:
				yield json.dumps(function_call.args or {})
			else:
				raise ToolCallsRequested(
					calls=[ToolCall(
						id=function_call.id or str(uuid.uuid4()), name=function_call.name or "",
						arguments=dict(function_call.args or {}),
					)],
					assistant_content={_REPLAY_PARTS_KEY: _consolidate_model_parts(replay_parts)},
				)

		if finish_reason == types.FinishReason.MAX_TOKENS:
			raise AIServiceProviderOutputTruncatedError(str(finish_reason))

	def get_input_tokens(self, prompt: str) -> int:
		with _handle_gemini_errors():
			response = self.__sync_client.models.count_tokens(model=self.__model_name, contents=prompt)
		return response.total_tokens