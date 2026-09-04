from __future__ import annotations

import json
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
    ToolCall,
    ToolCallsRequested,
    ToolSpec,
    content_to_text,
)
from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

# Fallback when tiktoken has no encoding for this model name (e.g. a
# llama.cpp/local model) — the closest OpenAI encoding still gives a
# reasonable estimate rather than an exact count.
DEFAULT_ENCODING_NAME = "cl100k_base"
# ~4 chars/token is the commonly cited English-text approximation — used
# only if tiktoken's own encoding files can't be loaded at all (e.g. an
# air-gapped llama.cpp deployment with no outbound network access).
CHARS_PER_TOKEN_ESTIMATE = 4
# The SDK's own default is 600s of read timeout and 2 silent retries. The
# cascade (ai/_providers/cascading_llm_provider.py) is the one retry policy here, and
# a minute of silence between streamed chunks is already a dead upstream.
REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)
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
        # Lazily resolved by get_input_tokens() — sentinel False means
        # "already tried, no encoding available" (see _get_encoding).
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

    def build_schema(self, tags: Dict[str, str]) -> Dict[str, Any]:
        properties: Dict[str, Dict[str, Any]] = {}
        required: List[str] = []

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

    def _build_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Two more provider-neutral message shapes beyond plain
        {role, content} — see LLMProvider.generate_stream_with_schema's own
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
                messages.append({
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": [
                        {
                            "id": call.id, "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                        }
                        for call in message["tool_calls"]
                    ],
                })
                continue

            role_out = "assistant" if role == "assistant" else "user"
            text_content: str = content_to_text(message.get("content"), "OpenAICompatible")
            # OpenAI-compatible chat templates (llama.cpp, LM Studio) assume
            # strict user/assistant alternation; consecutive same-role turns
            # (e.g. an AI-initiated opening message followed by attachment
            # priming) desync the template's role assignment instead of
            # erroring, so merge them rather than send them as separate
            # turns — never across a tool/tool_calls message, which always
            # stays standalone (see the two `continue`s above).
            if messages and messages[-1]["role"] == role_out:
                messages[-1]["content"] = f"{messages[-1]['content']}\n\n{text_content}"
            else:
                messages.append({"role": role_out, "content": text_content})
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

    async def generate_stream_with_schema(
        self,
        system_prompt: str,
        history: List[Dict[str, Any]],
        schema: Optional[Dict[str, str]] = None,
        on_metadata: Optional[MetadataCallback] = None,
        tools: Optional[List[ToolSpec]] = None,
    ) -> AsyncIterator[str]:
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._build_messages(history))

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
        openai_tools = self._build_tools(tools)
        if openai_tools:
            extra_kwargs["tools"] = openai_tools

        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        finish_reason: Optional[str] = None
        # Accumulated across chunks, keyed by the delta's own `index` (a
        # single response can request several tool calls in parallel,
        # each streamed as its own id/name once, then its `arguments`
        # dribbled in as a partial JSON string over further chunks).
        tool_call_chunks: Dict[int, Dict[str, Any]] = {}
        # Whatever text (if any) accompanied a tool-requesting response —
        # the provider-neutral assistant_content to replay in history.
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
                on_metadata("input_tokens", input_tokens)
                on_metadata("output_tokens", output_tokens)
            logger.info(
                f"OpenAI-compatible call finished: model={self._model_name} finish_reason={finish_reason} "
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
            # Not an HTTP-level failure at all — the request never reached a
            # server (e.g. connection refused because no local llama.cpp/LM
            # Studio instance is running on this base_url). Retrying won't
            # fix that mid-call, so this cascades immediately rather than
            # burning MAX_RETRIES backoff attempts against a closed port.
            raise AIServiceProviderPermanentError(f"Connection error: {exc}") from exc
        except Exception as exc:
            raise AIServiceError(f"Unexpected error: {exc}") from exc

        if finish_reason == "tool_calls":
            calls = [
                ToolCall(id=entry["id"], name=entry["name"], arguments=json.loads(entry["arguments"] or "{}"))
                for entry in tool_call_chunks.values()
            ]
            raise ToolCallsRequested(calls=calls, assistant_content=accumulated_text or None)

        if finish_reason == "length":
            raise AIServiceProviderOutputTruncatedError(finish_reason)
