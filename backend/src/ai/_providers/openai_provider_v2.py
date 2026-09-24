from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import tiktoken
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError

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
from ai.response_schema import Field, ObjectField
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


def _one_content(parts: list[dict]):
    """A single text part stays a plain string — byte for byte the request
    this provider has always sent; several stay content parts."""
    return parts[0]["text"] if len(parts) == 1 else parts


def _merged_content(existing, parts: list[dict]):
    """Two consecutive same-role turns as one. Two plain texts join with a
    blank line, as they always have; anything carrying real parts keeps
    them side by side instead."""
    if isinstance(existing, str) and len(parts) == 1:
        return f"{existing}\n\n{parts[0]['text']}"
    existing_parts = existing if isinstance(existing, list) else [{"type": "text", "text": existing}]
    return [*existing_parts, *parts]
DEFAULT_ENCODING_NAME = "cl100k_base"
CHARS_PER_TOKEN_ESTIMATE = 4
REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
SDK_MAX_RETRIES = 0


class OpenAICompatibleProvider(LLMProvider):

    def __init__(self, config: AIServiceConfig) -> None:
        super().__init__()
        base_url: str = (
            config.url.rstrip("/")
              if config.url else "http://localhost:8080/v1"
        )
        self._client: AsyncOpenAI = AsyncOpenAI(
            base_url=base_url,
            api_key=config.key or "lm-studio",
            timeout=REQUEST_TIMEOUT,
            max_retries=SDK_MAX_RETRIES,
        )
        self._model_name: str = config.model or "default-model"
        self._max_output_tokens: int = config.max_output_tokens
        self._encoding: tiktoken.Encoding | None | bool = None

    def _get_encoding(self) -> tiktoken.Encoding | None:
        if self._encoding is None:
            try:
                self._encoding = tiktoken.encoding_for_model(self._model_name)
            except Exception:
                try:
                    self._encoding = tiktoken.get_encoding(DEFAULT_ENCODING_NAME)
                except Exception:
                    logger.warning(
                        "No tiktoken encoding available for '%s' — falling back to a "
                        "character-count token estimate.", self._model_name,
                    )
                    self._encoding = False
        return self._encoding or None

    def get_input_tokens(self, prompt: str) -> int:
        encoding = self._get_encoding()
        if encoding is not None:
            return len(encoding.encode(prompt))
        return max(1, len(prompt) // CHARS_PER_TOKEN_ESTIMATE)

    def build_schema(self, schema: Dict[str, Field]) -> Dict[str, Any]:
        return ObjectField(schema).json_schema()

    def __build_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Two more provider-neutral message shapes beyond plain
        {role, content} — see LLMProvider.stream_json's own
        docstring: an assistant turn that asked for tools (translated to
        OpenAI's own `tool_calls` array, arguments re-encoded as a JSON
        string — OpenAI's own wire shape, unlike ToolCall.arguments'
        already-decoded dict), and a tool's own result (OpenAI already has
        a `role: "tool"` message shape near-identical to the neutral one,
        so this is close to a straight passthrough)."""
        messages: List[Dict[str, Any]] = []
        for message in history:
            role: str = message["role"]

            if role == "tool":
                messages.append({
                    "role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"],
                })
                continue

            if role == "assistant" and message.get("tool_calls"):
                assistant_text = message.get("content")
                messages.append({
                    "role": "assistant",
                    "content": None if isinstance(assistant_text, dict) else assistant_text,
                    "tool_calls": [
                        {
                            "id": call.id, "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                        }
                        for call in message["tool_calls"]
                    ],
                })
                continue

            if role not in ("user", "assistant"):
                continue

            content = message.get("content")
            parts = (
                [{"type": "text", "text": fragment} for fragment in content]
                if is_text_fragments(content)
                else [{"type": "text", "text": content_to_text(content, "OpenAICompatible")}]
            )
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] = _merged_content(messages[-1]["content"], parts)
            else:
                messages.append({"role": role, "content": _one_content(parts)})
        return messages

    @staticmethod
    def _build_tools(tools: Optional[List[ToolSpec]]) -> Optional[List[Dict[str, Any]]]:
        """None (not an empty list) when there's nothing to declare — kept
        entirely out of the request kwargs then, so a call with no tools
        is byte-for-byte the same request this provider always sent."""
        if not tools:
            return None
        return [
            {"type": "function", "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters}}
            for spec in tools
        ]

    async def stream_json(
        self,
        system_prompt: "str | SystemPrompt",
        history: List[Dict[str, Any]],
        schema: Optional[Dict[str, Field]] = None,
        on_metadata: Optional[MetadataCallback] = None,
        tools: Optional[List[ToolSpec]] = None,
        tool_round: int = 1,
        required_tools: Optional[List[ToolSpec]] = None,
    ) -> AsyncIterator[str]:
        messages: List[Dict[str, Any]] = [{"role": "system", "content": SystemPrompt.coerce(system_prompt).full_text()}]
        messages.extend(self.__build_messages(with_opening_turn(history)))

        extra_kwargs: Dict[str, Any] = {}
        if schema:
            extra_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response_schema",
                    "strict": True,
                    "schema": self.build_schema(schema),
                },
            }
        if required_tools:
            extra_kwargs["tools"] = self._build_tools(required_tools)
            extra_kwargs["tool_choice"] = "required"
        else:
            openai_tools = self._build_tools(tools)
            if openai_tools:
                extra_kwargs["tools"] = openai_tools

        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        finish_reason: Optional[str] = None
        tool_call_chunks: Dict[int, Dict[str, Any]] = {}
        accumulated_text = ""
        try:
            stream = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,  # type: ignore
                max_tokens=self._max_output_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **extra_kwargs,
            )

            async for chunk in stream:
                if chunk.usage is not None:
                    total_tokens = chunk.usage.total_tokens
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens
                    cache_details = getattr(chunk.usage, "prompt_tokens_details", None)
                    cache_read_tokens = getattr(cache_details, "cached_tokens", None) or 0
                if chunk.choices:
                    if chunk.choices[0].finish_reason is not None:
                        finish_reason = chunk.choices[0].finish_reason
                    if chunk.choices[0].delta.content:
                        accumulated_text += chunk.choices[0].delta.content
                        yield chunk.choices[0].delta.content
                    for tool_call_delta in chunk.choices[0].delta.tool_calls or []:
                        entry = tool_call_chunks.setdefault(
                            tool_call_delta.index, {"id": None, "name": None, "arguments": ""},
                        )
                        if tool_call_delta.id:
                            entry["id"] = tool_call_delta.id
                        if tool_call_delta.function is not None:
                            if tool_call_delta.function.name:
                                entry["name"] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                entry["arguments"] += tool_call_delta.function.arguments
            self._add_tokens(total_tokens)
            if on_metadata is not None:
                on_metadata("cache_read_tokens", cache_read_tokens)
                on_metadata("cache_creation_tokens", 0)
                on_metadata("input_tokens", input_tokens)
                on_metadata("output_tokens", output_tokens)
            logger.info(
                f"OpenAI-compatible call finished: model={self._model_name} finish_reason={finish_reason} "
                f"input_tokens={input_tokens} output_tokens={output_tokens} cache_read={cache_read_tokens} "
                f"total_tokens={total_tokens} max_output_tokens={self._max_output_tokens}"
            )

        except RateLimitError as exc:
            raise AIServiceProviderRateLimitedError(
                f"Rate limit exceeded: {exc}"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code in (503, 504):
                raise AIServiceProviderUnavailableError(
                    f"Service unavailable ({exc.status_code}): {exc}"
                ) from exc
            if exc.status_code == 400:
                raise AIServiceRequestError(
                    f"API error ({exc.status_code}): {exc}"
                ) from exc
            raise AIServiceProviderPermanentError(
                f"API error ({exc.status_code}): {exc}"
            ) from exc
        except APIConnectionError as exc:
            raise AIServiceProviderPermanentError(f"Connection error: {exc}") from exc
        except Exception as exc:
            raise AIServiceError(f"Unexpected error: {exc}") from exc

        if tool_call_chunks:
            try:
                calls = [
                    ToolCall(
                        id=entry["id"] or str(uuid.uuid4()), name=entry["name"],
                        arguments=json.loads(entry["arguments"] or "{}"),
                    )
                    for entry in tool_call_chunks.values()
                ]
            except json.JSONDecodeError as exc:
                raise AIServiceError(f"Malformed tool-call arguments: {exc}") from exc
            raise ToolCallsRequested(calls=calls, assistant_content=accumulated_text or None)

        if finish_reason == "length":
            raise AIServiceProviderOutputTruncatedError(finish_reason)
