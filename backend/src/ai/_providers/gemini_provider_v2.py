from __future__ import annotations

import asyncio
import threading
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

	def __format_history_and_config(
		self, system_prompt: str, history: list[dict[str, Any]], schema: dict[str, str]
	) -> tuple[list[types.Content], types.GenerateContentConfig]:
		contents: list[types.Content] = []

		for message in history:
			role: str = "model" if message["role"] == "assistant" else "user"
			text_content: str = content_to_text(message["content"], "Gemini")

			contents.append(
				types.Content(
					role=role,
					parts=[types.Part.from_text(text=text_content)],
				)
			)

		gen_config: types.GenerateContentConfig = types.GenerateContentConfig(
			system_instruction=system_prompt,
			max_output_tokens=self.__max_output_tokens,
			response_mime_type="application/json",
			response_schema=self.build_schema(schema),
		)

		return contents, gen_config

	async def generate_stream_with_schema(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		schema: dict[str, str] | None = None,
		on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None,
	) -> AsyncIterator[str]:
		if tools:
			# Native tool-calling isn't implemented for this provider yet
			# (see ai/_providers/anthropic_provider_v2.py for the one that
			# is) — never silently ignore a state's own declared tools.
			raise NotImplementedError(
				"GeminiProvider.generate_stream_with_schema: native tool-calling isn't implemented "
				"for this provider yet."
			)
		contents, config = self.__format_history_and_config(system_prompt, history, schema or {})

		total_tokens = 0
		input_tokens = 0
		output_tokens = 0
		finish_reason: types.FinishReason | None = None
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

		if finish_reason == types.FinishReason.MAX_TOKENS:
			raise AIServiceProviderOutputTruncatedError(str(finish_reason))

	def get_input_tokens(self, prompt: str) -> int:
		with _handle_gemini_errors():
			response = self.__sync_client.models.count_tokens(model=self.__model_name, contents=prompt)
		return response.total_tokens