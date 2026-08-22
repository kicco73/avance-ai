from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from typing import Any, AsyncIterator, Generator
import logging

from google import genai
from google.genai import types
from google.genai.errors import APIError

from cascade import OnRetry
from ai.llm_provider import (
	AIServiceConfig,
	AIServiceError,
	AIServiceProviderRateLimitedError,
	AIServiceProviderUnavailableError,
	LLMProviderWithSchema,
	content_to_text,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: int = 1024

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
		raise AIServiceError(
			f"Error from the Gemini API (status {code}): {message}"
		) from exc
	except Exception as exc:
		raise AIServiceError(f"Unexpected error from the Gemini API: {exc}") from exc

	dict[str, tuple[type, str]]

class GeminiProvider(LLMProviderWithSchema):
	def __init__(self, config: AIServiceConfig) -> None:
		self._api_key: str = config.key
		self._base_url: str | None = config.url
		self._model_name: str = config.model
		# genai.Client's async transport lazily binds internal
		# asyncio.Lock/Event objects to whichever event loop first uses
		# it. This provider is a single app-wide instance shared by both
		# the main FastAPI loop and every JobQueue worker thread's own
		# loop (see jobs/job_queue.py) — reusing one Client across those
		# raises "... is bound to a different event loop". A client per
		# loop avoids it; self._clients_lock guards concurrent first-use
		# from different worker threads.
		self._clients: dict[asyncio.AbstractEventLoop, genai.Client] = {}
		self._clients_lock = threading.Lock()

	def _client(self) -> genai.Client:
		loop = asyncio.get_running_loop()
		client = self._clients.get(loop)
		if client is None:
			with self._clients_lock:
				client = self._clients.get(loop)
				if client is None:
					client = genai.Client(
						api_key=self._api_key,
						http_options={"base_url": self._base_url} if self._base_url else None,
					)
					self._clients[loop] = client
		return client

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

	def _format_history_and_config(
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
			max_output_tokens=MAX_OUTPUT_TOKENS,
			response_mime_type="application/json",
			response_schema=self.build_schema(schema),
		)

		return contents, gen_config

	async def generate_stream_with_schema(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		schema: dict[str, str] | None = None
	) -> AsyncIterator[str]:

		contents, config = self._format_history_and_config(system_prompt, history, schema or {})

		with _handle_gemini_errors():
			response_stream = await self._client().aio.models.generate_content_stream(
				model=self._model_name,
				contents=contents,
				config=config,
			)

			async for chunk in response_stream:
				if not chunk.text:
					continue
				yield chunk.text

