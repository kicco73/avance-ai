"""LLM provider backed by OpenAI-compatible Chat Completions APIs.

Compatible with:
- OpenAI
- llama.cpp / llama-server
- other OpenAI-compatible Chat Completions endpoints
"""

from __future__ import annotations

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
from openai.types.chat.completion_create_params import ResponseFormat

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

	def build_schema(
		self,
		tags: dict[str, str],
	) -> dict[str, Any]:
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

	def _format_messages(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
	) -> list[ChatCompletionMessageParam]:
		messages: list[ChatCompletionMessageParam] = [
			{
				"role": "system",
				"content": system_prompt,
			}
		]

		for message in history:
			role: str = message["role"]

			if role not in ("user", "assistant"):
				continue

			text_content: str = content_to_text(
				message["content"],
				"OpenAI",
			)

			if role == "user":
				messages.append(
					{
						"role": "user",
						"content": text_content,
					}
				)
			else:
				messages.append(
					{
						"role": "assistant",
						"content": text_content,
					}
				)

		return messages

	def _response_format(
		self,
		schema: dict[str, str],
	) -> ResponseFormat:
		"""
		Build a schema-constrained JSON response format.

		Using json_object + schema is intentionally chosen because it is
		supported by llama.cpp and is also accepted by OpenAI-compatible
		Chat Completions implementations.
		"""
		response_schema: dict[str, Any] = self.build_schema(schema)

		response_format: dict[str, Any] = {
			"type": "json_object",
			"schema": response_schema,
		}

		return cast(ResponseFormat, response_format)

	async def generate_stream_with_schema(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		schema: dict[str, str] | None = None,
	) -> AsyncIterator[str]:
		messages: list[ChatCompletionMessageParam] = self._format_messages(
			system_prompt,
			history,
		)

		response_format: ResponseFormat = self._response_format(
			schema or {}
		)

		with _handle_openai_errors():
			response_stream = await self._async_client.chat.completions.create(
				model=self._model_name,
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