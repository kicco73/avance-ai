"""LLM provider backed by the Anthropic API (Claude)."""

from __future__ import annotations

import asyncio
import threading
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
from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

CLAUDE_DEFAULT_MODEL: str = "claude-sonnet-5"
# httpx semantics: connect/write/pool budget, and the longest silence
# tolerated *between* streamed chunks — not a cap on the whole reply.
REQUEST_TIMEOUT_SECONDS: float = 30.0
# The SDK's own retries are off: the cascade (ai/_providers/cascading_llm_provider.py)
# is the one retry policy, so a 503 surfaces here at once instead of after
# 2 silent SDK attempts stacked under the cascade's own 5.
SDK_MAX_RETRIES: int = 0
# stop_reason values meaning the response was cut short rather than
# completing on its own — see AIServiceProviderOutputTruncatedError.
_TRUNCATED_STOP_REASONS = ("max_tokens", "model_context_window_exceeded")

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
			f"The Anthropic API rate limit was exceeded (status 429): {exc}"
		) from exc

	except anthropic.APIStatusError as exc:
		status_code: int = exc.status_code

		if status_code in (503, 502, 504):
			raise AIServiceProviderUnavailableError(
				f"The Anthropic service is temporarily unavailable "
				f"(status {status_code})."
			) from exc

		if status_code == 400:
			raise AIServiceRequestError(
				f"Error from the Anthropic API "
				f"(status {status_code}): {exc}"
			) from exc

		raise AIServiceProviderPermanentError(
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


class AnthropicProvider(LLMProvider):
	def __init__(self, config: AIServiceConfig) -> None:
		super().__init__()
		self._model_name: str = config.model or CLAUDE_DEFAULT_MODEL
		self._max_output_tokens: int = config.max_output_tokens

		self._api_key: str = config.key
		# One AsyncAnthropic per event loop, same reasoning as
		# GeminiProvider.__client: this provider is a single app-wide
		# instance driven from the main FastAPI loop, from every JobQueue
		# worker's own long-lived loop, and from the one-shot loop each
		# PromptContext._run_sync spins up. An httpx connection pool shared
		# across loops reuses keep-alive sockets opened on another loop —
		# in practice sporadic APIConnectionError ("Unable to reach the
		# Anthropic API") under concurrent test replays, reproduced by
		# tests/test_provider_event_loops.py. `_clients_lock` guards the
		# first use from different threads; closed loops are pruned when
		# a new one shows up, so the one-shot loops never pile up.
		self._async_clients: dict[asyncio.AbstractEventLoop, anthropic.AsyncAnthropic] = {}
		self._clients_lock = threading.Lock()
		# get_input_tokens() calls messages.count_tokens synchronously —
		# a plain sync client, rather than awaiting the async one above,
		# keeps that method callable with no running event loop.
		self._sync_client: anthropic.Anthropic = anthropic.Anthropic(
			api_key=config.key,
			timeout=REQUEST_TIMEOUT_SECONDS,
			max_retries=SDK_MAX_RETRIES,
		)

	def _new_async_client(self) -> anthropic.AsyncAnthropic:
		return anthropic.AsyncAnthropic(
			api_key=self._api_key,
			timeout=REQUEST_TIMEOUT_SECONDS,
			max_retries=SDK_MAX_RETRIES,
		)

	def _async_client_for_current_loop(self) -> anthropic.AsyncAnthropic:
		loop = asyncio.get_running_loop()
		client = self._async_clients.get(loop)
		if client is None:
			with self._clients_lock:
				client = self._async_clients.get(loop)
				if client is None:
					for stale in [candidate for candidate in self._async_clients if candidate.is_closed()]:
						del self._async_clients[stale]
					client = self._new_async_client()
					self._async_clients[loop] = client
		return client

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
		"""Two more provider-neutral message shapes beyond plain
		{role, content} — see LLMProvider.generate_stream_with_schema's own
		docstring: an assistant turn that asked for tools (translated to
		one text block, if it said anything, plus one tool_use block per
		call), and a tool's own result (translated to a *user* message
		holding a tool_result block — Anthropic has no separate "tool"
		role; a result is something the user's side of the conversation
		hands back)."""
		messages: list[MessageParam] = []

		for message in history:
			role: str = message["role"]

			if role == "tool":
				messages.append({
					"role": "user",
					"content": [{
						"type": "tool_result",
						"tool_use_id": message["tool_call_id"],
						"content": message["content"],
					}],
				})
				continue

			if role == "assistant" and message.get("tool_calls"):
				blocks: list[Any] = []
				assistant_text = message.get("content")
				if assistant_text:
					blocks.append({"type": "text", "text": str(assistant_text)})
				for call in message["tool_calls"]:
					blocks.append({
						"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments,
					})
				messages.append({"role": "assistant", "content": blocks})
				continue

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

	@staticmethod
	def _build_tools(tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
		"""None (not an empty list) when there's nothing to declare — kept
		entirely out of the request kwargs then, so a call with no tools
		is byte-for-byte the same request this provider always sent."""
		if not tools:
			return None
		return [
			{"name": spec.name, "description": spec.description, "input_schema": spec.parameters}
			for spec in tools
		]

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
		on_metadata: MetadataCallback | None = None,
		tools: list[ToolSpec] | None = None,
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

		anthropic_tools = self._build_tools(tools)

		# tools omitted entirely (not passed as None) when there aren't
		# any — matches _build_tools' own contract; the SDK's own `tools`
		# param type doesn't even accept None, only Iterable[...] or Omit.
		stream_kwargs: dict[str, Any] = {
			"model": self._model_name,
			"max_tokens": self._max_output_tokens,
			"system": system_blocks,
			"messages": messages,
			"output_config": output_config,
		}
		if anthropic_tools:
			stream_kwargs["tools"] = anthropic_tools

		stop_reason: str | None = None
		final_content: list[Any] = []
		try:
			with _handle_anthropic_errors():
				stream_manager = self._async_client_for_current_loop().messages.stream(**stream_kwargs)

			async with stream_manager as stream:
				async for text in stream.text_stream:
					if text:
						yield text
				final_message = await stream.get_final_message()
				usage = final_message.usage
				self._add_tokens(usage.input_tokens + usage.output_tokens)
				if on_metadata is not None:
					on_metadata("input_tokens", usage.input_tokens)
					on_metadata("output_tokens", usage.output_tokens)
				stop_reason = final_message.stop_reason
				final_content = final_message.content
				logger.info(
					f"Anthropic call finished: model={self._model_name} stop_reason={stop_reason} "
					f"output_tokens={usage.output_tokens} max_output_tokens={self._max_output_tokens}"
				)

		except (
			AIServiceError,
			AIServiceProviderRateLimitedError,
			AIServiceProviderUnavailableError,
		):
			raise

		except Exception as exc:
			with _handle_anthropic_errors():
				raise exc

		if stop_reason == "tool_use":
			calls = [
				ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
				for block in final_content if getattr(block, "type", None) == "tool_use"
			]
			# Whatever the model said before/alongside asking for these
			# calls (often nothing, under a JSON-schema response) — the
			# provider-neutral assistant_content to replay in history.
			assistant_text = "".join(
				block.text for block in final_content if getattr(block, "type", None) == "text"
			)
			raise ToolCallsRequested(calls=calls, assistant_content=assistant_text or None)

		if stop_reason in _TRUNCATED_STOP_REASONS:
			raise AIServiceProviderOutputTruncatedError(stop_reason)

	def get_input_tokens(self, prompt: str) -> int:
		with _handle_anthropic_errors():
			response = self._sync_client.messages.count_tokens(
				model=self._model_name,
				messages=[{"role": "user", "content": prompt}],
			)
		return response.input_tokens