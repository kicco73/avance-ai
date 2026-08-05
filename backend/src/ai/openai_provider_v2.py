"""LLM provider backed by OpenAI (or any OpenAI-compatible API)."""

from __future__ import annotations

from contextlib import contextmanager
from http import HTTPStatus
from typing import Any, AsyncIterator, Generator

from openai import (
	APIConnectionError,
	APIError,
	APIStatusError,
	AsyncOpenAI,
	RateLimitError,
)
from openai.types.responses.response_input_param import ResponseInputParam
from openai.types.responses.response_text_config_param import (
	ResponseTextConfigParam,
)

from ai.llm_provider import (
	AIServiceConfig,
	AIServiceError,
	AIServiceProviderRateLimitedError,
	AIServiceProviderUnavailableError,
	LLMProviderWithSchema,
	content_to_text,
)

MAX_OUTPUT_TOKENS: int = 1024


@contextmanager
def _handle_openai_errors() -> Generator[None, None, None]:
	"""Centralized handling and remapping of OpenAI SDK exceptions."""
	try:
		yield

	except RateLimitError as exc:
		raise AIServiceProviderRateLimitedError(
			f"The OpenAI API rate limit was exceeded (status 429): {exc}"
		) from exc

	except APIStatusError as exc:
		status_code: int = exc.status_code

		if status_code in (
			HTTPStatus.SERVICE_UNAVAILABLE,
			HTTPStatus.GATEWAY_TIMEOUT,
		):
			raise AIServiceProviderUnavailableError(
				f"The OpenAI service is temporarily unavailable "
				f"(status {status_code}): {exc}"
			) from exc

		raise AIServiceError(
			f"Error from the OpenAI API "
			f"(status {status_code}): {exc}"
		) from exc

	except APIConnectionError as exc:
		raise AIServiceProviderUnavailableError(
			f"Could not connect to the AI service endpoint: {exc}"
		) from exc

	except APIError as exc:
		raise AIServiceError(
			f"Unexpected error from the OpenAI API: {exc}"
		) from exc

	except Exception as exc:
		raise AIServiceError(
			f"Unhandled exception from the OpenAI API: {exc}"
		) from exc


class OpenAIProvider(LLMProviderWithSchema):
	def __init__(self, config: AIServiceConfig) -> None:
		self._async_client: AsyncOpenAI = AsyncOpenAI(
			api_key=config.key,
			base_url=config.url,
		)
		self._model_name: str = config.model

	def build_schema(self, tags: dict[str, str]) -> dict[str, Any]:
		properties: dict[str, dict[str, Any]] = {}
		required: list[str] = []

		for name, description in tags.items():
			properties[name] = {
				"type": "string",
				"description": description,
			}
			required.append(name)

		return {
			"type": "object",
			"properties": properties,
			"required": required,
			"additionalProperties": False,
		}

	def _format_history(
		self,
		history: list[dict[str, Any]],
	) -> ResponseInputParam:
		input_messages: ResponseInputParam = []

		for message in history:
			role: str = message["role"]

			if role not in ("user", "assistant"):
				continue

			text_content: str = content_to_text(
				message["content"],
				"OpenAI",
			)

			input_messages.append(
				{
					"role": role,
					"content": text_content,
				}
			)

		return input_messages

	def _response_text_config(
		self,
		schema: dict[str, str],
	) -> ResponseTextConfigParam:
		response_schema: dict[str, Any] = self.build_schema(schema)

		return {
			"format": {
				"type": "json_schema",
				"name": "ai_service_response",
				"strict": True,
				"schema": response_schema,
			}
		}

	async def generate_stream_with_schema(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		schema: dict[str, str] | None = None,
	) -> AsyncIterator[str]:
		input_messages: ResponseInputParam = self._format_history(
			history
		)

		text_config: ResponseTextConfigParam = (
			self._response_text_config(schema or {})
		)

		with _handle_openai_errors():
			response_stream = await self._async_client.responses.create(
				model=self._model_name,
				instructions=system_prompt,
				input=input_messages,
				max_output_tokens=MAX_OUTPUT_TOKENS,
				text=text_config,
				stream=True,
			)

			async for event in response_stream:
				if event.type != "response.output_text.delta":
					continue

				if not event.delta:
					continue

				yield event.delta