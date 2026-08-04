from __future__ import annotations

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


class GeminiProvider(LLMProviderWithSchema):
	def __init__(self, config: AIServiceConfig) -> None:
		self._client: genai.Client = genai.Client(
			api_key=config.key,
			http_options={"base_url": config.url} if config.url else None,
		)
		self._model_name: str = config.model
		self.build_schema({}, {})

	def build_schema(self, priority_tags: dict[str, tuple[type, str]], tags: dict[str, tuple[type, str]]) -> None:

		properties: dict[str, dict] = {}
		required: list[str] = []

		for name, (_python_type, description) in priority_tags.items():
			properties[name] = {
				"type": "STRING",
				"description": description,
			}
			required.append(name)

		properties["text"] = {
			"type": "STRING",
			"description": "Extended textual response for the user.",
		}
		required.append("text")

		for name, (_python_type, description) in tags.items():
			properties[name] = {
				"type": "STRING",
				"description": description,
				"nullable": False,
			}
			required.append(name)

		self._app_response_schema = {
			"type": "OBJECT",
			"properties": properties,
			"required": required,
		}

	def _format_history_and_config(
		self, system_prompt: str, history: list[dict[str, Any]]
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

		field_order = self._field_order()
		order_instruction = (
			"Respond with the structured JSON object described by the response "
			"schema, filling in its fields in this order: "
			+ ", ".join(f"'{name}'" for name in field_order) + "."
		)
		full_system_prompt = f"{system_prompt}\n\n{order_instruction}"

		gen_config: types.GenerateContentConfig = types.GenerateContentConfig(
			system_instruction=full_system_prompt,
			max_output_tokens=MAX_OUTPUT_TOKENS,
			response_mime_type="application/json",
			response_schema=self._app_response_schema,
		)

		return contents, gen_config

	def _field_order(self) -> list[str]:
		return list(self._app_response_schema["properties"].keys())

	async def generate_stream(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_retry: OnRetry | None = None,
	) -> AsyncIterator[str]:

		contents, config = self._format_history_and_config(system_prompt, history)

		with _handle_gemini_errors():
			response_stream = await self._client.aio.models.generate_content_stream(
				model=self._model_name,
				contents=contents,
				config=config,
			)

			async for chunk in response_stream:
				if not chunk.text:
					continue
				yield chunk.text

