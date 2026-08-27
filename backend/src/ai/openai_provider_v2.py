from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

import tiktoken
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError

from ai.llm_provider import (
    AIServiceConfig,
    AIServiceError,
    AIServiceProviderOutputTruncatedError,
    AIServiceProviderPermanentError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    LLMProviderWithSchema,
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


class OpenAICompatibleProvider(LLMProviderWithSchema):

    def __init__(self, config: AIServiceConfig) -> None:
        super().__init__()
        base_url: str = (
            config.url.rstrip("/")
              if config.url else "http://localhost:8080/v1"
        )
        self._client: AsyncOpenAI = AsyncOpenAI(
            base_url=base_url,
            api_key=config.key or "lm-studio",
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

    async def generate_stream_with_schema(
        self,
        system_prompt: str,
        history: List[Dict[str, Any]],
        schema: Optional[Dict[str, str]] = None,
    ) -> AsyncIterator[str]:
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        for message in history:
            role: str = (
                "assistant" if message.get("role") == "assistant" else "user"
            )
            text_content: str = content_to_text(
                message.get("content"), "OpenAICompatible"
            )
            # OpenAI-compatible chat templates (llama.cpp, LM Studio) assume
            # strict user/assistant alternation; consecutive same-role turns
            # (e.g. an AI-initiated opening message followed by attachment
            # priming) desync the template's role assignment instead of
            # erroring, so merge them rather than send them as separate turns.
            if messages[-1]["role"] == role:
                messages[-1]["content"] = f"{messages[-1]['content']}\n\n{text_content}"
            else:
                messages.append({"role": role, "content": text_content})

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

        total_tokens = 0
        finish_reason: Optional[str] = None
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
                if chunk.choices:
                    if chunk.choices[0].finish_reason is not None:
                        finish_reason = chunk.choices[0].finish_reason
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            self._add_tokens(total_tokens)

        except RateLimitError as exc:
            raise AIServiceProviderRateLimitedError(
                f"Rate limit exceeded: {exc}"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code in (503, 504):
                raise AIServiceProviderUnavailableError(
                    f"Service unavailable ({exc.status_code}): {exc}"
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

        if finish_reason == "length":
            raise AIServiceProviderOutputTruncatedError(finish_reason)
