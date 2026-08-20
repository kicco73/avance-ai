"""LLM provider backed by the Anthropic API (Claude)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, AsyncIterator, Generator

import anthropic
from anthropic.types import (
	CacheControlEphemeralParam,
	MessageParam,
	TextBlockParam,
)

from ai.llm_provider import (
	AIServiceConfig,
	AIServiceError,
	AIServiceProviderRateLimitedError,
	AIServiceProviderUnavailableError,
	LLMProviderWithSchema,
	content_to_text,
)

CLAUDE_DEFAULT_MODEL: str = "claude-sonnet-5"
MAX_OUTPUT_TOKENS: int = 1024
REQUEST_TIMEOUT_SECONDS: float = 30.0

CACHE_CONTROL: CacheControlEphemeralParam = {"type": "ephemeral"}


@contextmanager
def _handle_anthropic_errors() -> Generator[None, None, None]:
	"""Centralized handling and remapping of Anthropic SDK exceptions."""
	try:
		yield

	except anthropic.APITimeoutError as exc:
		raise AIServiceProviderUnavailableError(
			"Timeout while calling the Anthropic API."
		) from exc

	except anthropic.RateLimitError as exc:
		raise AIServiceProviderRateLimitedError(
			"The Anthropic API rate limit was exceeded (status 429)."
		) from exc

	except anthropic.APIStatusError as exc:
		status_code: int = exc.status_code

		if status_code in (503, 502, 504):
			raise AIServiceProviderUnavailableError(
				f"The Anthropic service is temporarily unavailable "
				f"(status {status_code})."
			) from exc

		raise AIServiceError(
			f"Error from the Anthropic API "
			f"(status {status_code}): {exc}"
		) from exc

	except anthropic.APIConnectionError as exc:
		raise AIServiceProviderUnavailableError(
			"Unable to reach the Anthropic API. "
			"Check your network connection."
		) from exc

	except anthropic.APIError as exc:
		raise AIServiceError(
			f"Unexpected error from the Anthropic API: {exc}"
		) from exc

	except Exception as exc:
		raise AIServiceError(
			f"Unhandled exception from the Anthropic API: {exc}"
		) from exc


class AnthropicProvider(LLMProviderWithSchema):
	def __init__(self, config: AIServiceConfig) -> None:
		self._model_name: str = config.model or CLAUDE_DEFAULT_MODEL

		self._async_client: anthropic.AsyncAnthropic = (
			anthropic.AsyncAnthropic(
				api_key=config.key,
				timeout=REQUEST_TIMEOUT_SECONDS,
			)
		)

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

	def _build_messages(
		self,
		history: list[dict[str, Any]],
	) -> list[MessageParam]:
		messages: list[MessageParam] = []

		for message in history:
			role: str = message["role"]

			if role not in ("user", "assistant"):
				continue

			content: Any = message["content"]

			if isinstance(content, str):
				messages.append(
					{
						"role": role,
						"content": content,
					}
				)
				continue

			text_content: str = content_to_text(
				content,
				"Anthropic",
			)

			messages.append(
				{
					"role": role,
					"content": text_content,
				}
			)

		return messages

	def _build_system(
		self,
		system_prompt: str,
	) -> list[TextBlockParam]:
		return [
			{
				"type": "text",
				"text": system_prompt,
				"cache_control": CACHE_CONTROL,
			}
		]

	def _build_output_config(
		self,
		schema: dict[str, str],
	) -> Any:
		response_schema: dict[str, Any] = self.build_schema(
			schema
		)

		# Any: Anthropic's SDK types don't expose the dynamic JSON-schema
		# shape precisely enough to type this without false positives.
		return {
			"format": {
				"type": "json_schema",
				"schema": response_schema,
			}
		}

	async def generate_stream_with_schema(
		self,
		system_prompt: str,
		history: list[dict[str, Any]],
		schema: dict[str, str] | None = None,
	) -> AsyncIterator[str]:
		messages: list[MessageParam] = self._build_messages(
			history
		)

		system_blocks: list[TextBlockParam] = self._build_system(
			system_prompt
		)

		output_config: Any = self._build_output_config(
			schema or {}
		)

		try:
			with _handle_anthropic_errors():
				stream_manager = self._async_client.messages.stream(
					model=self._model_name,
					max_tokens=MAX_OUTPUT_TOKENS,
					system=system_blocks,
					messages=messages,
					output_config=output_config,
				)

			async with stream_manager as stream:
				async for text in stream.text_stream:
					if text:
						yield text

		except (
			AIServiceError,
			AIServiceProviderRateLimitedError,
			AIServiceProviderUnavailableError,
		):
			raise

		except Exception as exc:
			with _handle_anthropic_errors():
				raise exc