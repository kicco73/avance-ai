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

from cascade import OnRetry
from logging_factory import LoggerFactory
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
	ToolCall,
	ToolCallsRequested,
	ToolSpec,
	content_to_text,
)

logger = LoggerFactory.get_logger(__name__)

# google-genai sends no timeout at all unless told to (HttpOptions.timeout
# defaults to None, passed straight through to httpx) — an upstream that
# stops answering would otherwise hang a chat turn, or a JobQueue worker,
# forever. Milliseconds, per HttpOptions; httpx applies it to connect and
# to the longest silence between streamed chunks, not to the whole reply.
REQUEST_TIMEOUT_MS: int = 60_000

# response_schema (controlled JSON generation) and tools (function
# calling) don't reliably combine on this provider — see
# generate_stream_with_schema's own docstring for the "respond as a
# tool" fallback this name identifies: a synthetic tool whose own
# parameters are the schema's fields, forced whenever the model isn't
# asking for a real one, standing in for a genuine structured response.
_RESPOND_TOOL_NAME = "respond"


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
		# genai.Client's async transport lazily binds internal
		# asyncio.Lock/Event objects to whichever event loop first uses
		# it. This provider is a single app-wide instance shared by both
		# the main FastAPI loop's own long-lived loop and every one-shot
		# loop PromptContext._run_sync spins up per source.prompt() call
		# (a fresh asyncio.run() — and thus a fresh, never-reused loop —
		# every time) — reusing one Client across those raises "... is
		# bound to a different event loop". A client per loop avoids it;
		# self.__clients_lock guards concurrent first-use from different
		# threads. Without __prune_closed_loops below, every such one-shot
		# loop would leave its own entry (and Client) behind forever once
		# asyncio.run() closes it — this dict would grow without bound.
		self.__clients: dict[asyncio.AbstractEventLoop, genai.Client] = {}
		self.__clients_lock = threading.Lock()
		# get_input_tokens() only ever uses the sync (non-.aio) surface,
		# which isn't event-loop-bound the way the async transport above
		# is — one plain client, built once, is enough.
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

	def build_schema(self, tags: dict[str, str]) -> dict:
		properties: dict[str, dict] = {}
		required: list[str] = []

		for name, description in tags.items():
			properties[name] = {
				"type": "STRING",
				"description": description,
				"nullable": False,
			}
			required.append(name)

		return {
			"type": "OBJECT",
			"properties": properties,
			"required": required,
		}

	def __build_contents(self, history: list[dict[str, Any]]) -> list[types.Content]:
		"""Two more provider-neutral message shapes beyond plain
		{role, content} — see LLMProvider.generate_stream_with_schema's own
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
				parts: list[types.Part] = []
				text = message.get("content")
				if text:
					parts.append(types.Part.from_text(text=str(text)))
				for call in message["tool_calls"]:
					call_name_by_id[call.id] = call.name
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
			text_content: str = content_to_text(message["content"], "Gemini")
			contents.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=text_content)]))

		return contents

	@staticmethod
	def __schema_to_gemini_parameters(parameters: dict) -> dict:
		"""ToolSpec.parameters is plain JSON Schema (lowercase types,
		ToolSet.specs' own contract: an object of all-required plain
		strings) — Gemini's own Schema type uses uppercase type names, the
		same convention build_schema above already follows for
		response_schema."""
		return {
			"type": "OBJECT",
			"properties": {name: {"type": "STRING"} for name in parameters.get("properties", {})},
			"required": list(parameters.get("required", [])),
		}

	def __respond_tool_declaration(self, schema: dict[str, str]) -> types.FunctionDeclaration:
		return types.FunctionDeclaration(
			name=_RESPOND_TOOL_NAME,
			description=(
				"Call this with your final structured reply once you have everything you need — "
				"its own arguments *are* the answer, one per field below."
			),
			parameters={
				"type": "OBJECT",
				"properties": {
					name: {"type": "STRING", "description": description} for name, description in schema.items()
				},
				"required": list(schema.keys()),
			},
		)

	def __tool_declarations(self, tools: list[ToolSpec], schema: dict[str, str]) -> list[types.FunctionDeclaration]:
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

	async def generate_stream_with_schema(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		schema: dict[str, str] | None = None,
		on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None,
	) -> AsyncIterator[str]:
		contents = self.__build_contents(history)
		schema = schema or {}

		if tools:
			# response_schema (controlled JSON generation) and tools
			# (function calling) don't reliably combine on this provider —
			# never assume they do. Fold the structured answer itself into
			# a synthetic "respond" tool instead (see _RESPOND_TOOL_NAME),
			# forced via tool_config so every turn ends in *some* function
			# call — a real one, or "respond" once nothing else is needed.
			config: types.GenerateContentConfig = types.GenerateContentConfig(
				system_instruction=system_prompt,
				max_output_tokens=self.__max_output_tokens,
				tools=[types.Tool(function_declarations=self.__tool_declarations(tools, schema))],
				tool_config=types.ToolConfig(
					function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY),
				),
			)
		else:
			config = types.GenerateContentConfig(
				system_instruction=system_prompt,
				max_output_tokens=self.__max_output_tokens,
				response_mime_type="application/json",
				response_schema=self.build_schema(schema),
			)

		total_tokens = 0
		input_tokens = 0
		output_tokens = 0
		finish_reason: types.FinishReason | None = None
		# Gemini delivers a function call as one complete part, never
		# streamed argument-by-argument the way OpenAI's deltas are — so
		# there's nothing to accumulate across chunks, just the latest one seen.
		function_call: types.FunctionCall | None = None
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
				if chunk.candidates and chunk.candidates[0].finish_reason is not None:
					finish_reason = chunk.candidates[0].finish_reason
				if tools:
					content = chunk.candidates[0].content if chunk.candidates else None
					for part in (content.parts if content else None) or []:
						if part.function_call is not None:
							function_call = part.function_call
					continue
				if not chunk.text:
					continue
				yield chunk.text
		self._add_tokens(total_tokens)
		if on_metadata is not None:
			on_metadata("input_tokens", input_tokens)
			on_metadata("output_tokens", output_tokens)
		logger.info(
			f"Gemini call finished: model={self.__model_name} finish_reason={finish_reason} "
			f"total_tokens={total_tokens} max_output_tokens={self.__max_output_tokens}"
		)

		if tools and function_call is not None:
			if function_call.name == _RESPOND_TOOL_NAME:
				# The model's own final structured answer, disguised as a
				# tool call — yielded as the same JSON text a schema-based
				# response would have produced, so AiService's own
				# partial-JSON parser downstream needs no changes at all.
				yield json.dumps(function_call.args or {})
			else:
				raise ToolCallsRequested(
					calls=[ToolCall(
						id=function_call.id or str(uuid.uuid4()), name=function_call.name or "",
						arguments=dict(function_call.args or {}),
					)],
					# Gemini's own function-call parts carry no separate
					# "text said alongside this call" — nothing to replay.
					assistant_content=None,
				)

		if finish_reason == types.FinishReason.MAX_TOKENS:
			raise AIServiceProviderOutputTruncatedError(str(finish_reason))

	def get_input_tokens(self, prompt: str) -> int:
		with _handle_gemini_errors():
			response = self.__sync_client.models.count_tokens(model=self.__model_name, contents=prompt)
		return response.total_tokens