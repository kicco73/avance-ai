"""LLM provider backed by OpenAI (or any OpenAI-compatible API like llama.cpp)."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from http import HTTPStatus
from typing import Any, AsyncIterator, Generator, cast

from openai import (
	APIConnectionError,
	APIError,
	APIStatusError,
	AsyncOpenAI,
	RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam

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
		self._model: str = config.model

		self.build_schema({}, {})

	def build_schema(
		self,
		priority_tags: dict[str, tuple[type, str]],
		tags: dict[str, tuple[type, str]],
	) -> None:
		"""Build the JSON schema used for structured responses."""
		properties: dict[str, dict[str, Any]] = {}
		required: list[str] = []

		for name, (_python_type, description) in priority_tags.items():
			properties[name] = {
				"type": "string",
				"description": description,
			}
			required.append(name)

		properties["text"] = {
			"type": "string",
			"description": "Extended textual response for the user.",
		}
		required.append("text")

		for name, (_python_type, description) in tags.items():
			properties[name] = {
				"type": "string",
				"description": description,
			}
			required.append(name)

		self._app_response_schema: dict[str, Any] = {
			"type": "object",
			"properties": properties,
			"required": required,
			"additionalProperties": False,
		}

	def _field_order(self) -> list[str]:
		return list(self._app_response_schema["properties"].keys())

	def _format_messages(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
	) -> list[ChatCompletionMessageParam]:
		"""Format system prompt and history for OpenAI Chat Completions."""
		field_order: list[str] = self._field_order()

		order_instruction: str = (
			"Respond with the structured JSON object described by the response "
			"schema, filling in its fields in this order: "
			+ ", ".join(f"'{name}'" for name in field_order)
			+ "."
		)

		full_system_prompt: str = (
			f"{system_prompt}\n\n{order_instruction}"
		)

		messages: list[ChatCompletionMessageParam] = [
			cast(
				ChatCompletionMessageParam,
				{
					"role": "system",
					"content": full_system_prompt,
				},
			)
		]

		for message in history:
			role: str = message["role"]

			if role not in ("user", "assistant"):
				continue

			text_content: str = content_to_text(
				message["content"],
				"OpenAI",
			)

			messages.append(
				cast(
					ChatCompletionMessageParam,
					{
						"role": role,
						"content": text_content,
					},
				)
			)

		return messages

	def _response_format(self) -> Any:
		"""
		Build the structured-output response format.

		The OpenAI SDK accepts a JSON Schema response format here.
		The schema itself is dynamic, so keeping this value as Any avoids
		fighting the generated SDK overloads with Pylance.
		"""
		return {
			"type": "json_schema",
			"json_schema": {
				"name": "ai_service_response",
				"strict": True,
				"schema": self._app_response_schema,
			},
		}

	async def generate_stream(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		on_retry: OnRetry | None = None,
	) -> AsyncIterator[str]:
		messages: list[ChatCompletionMessageParam] = self._format_messages(
			system_prompt,
			history,
		)

		response_format: Any = self._response_format()

		with _handle_openai_errors():
			response_stream = await self._async_client.chat.completions.create(
				model=self._model,
				messages=messages,
				max_tokens=MAX_OUTPUT_TOKENS,
				response_format=response_format,
				stream=True,
			)

			async for chunk in response_stream:
				if not chunk.choices:
					continue

				content: str | None = chunk.choices[0].delta.content

				if content:
					yield content

