from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError

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


class OpenAICompatibleProvider(LLMProviderWithSchema):

    def __init__(self, config: AIServiceConfig) -> None:
        base_url: str = (
            config.url.rstrip("/")
              if config.url else "http://localhost:8080/v1"
        )
        self._client: AsyncOpenAI = AsyncOpenAI(
            base_url=base_url,
            api_key=config.key or "lm-studio",
        )
        self._model_name: str = config.model or "default-model"

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

        try:
            stream = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,  # type: ignore
                max_tokens=MAX_OUTPUT_TOKENS,
                stream=True,
                **extra_kwargs,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except RateLimitError as exc:
            raise AIServiceProviderRateLimitedError(
                f"Rate limit exceeded: {exc}"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code in (503, 504):
                raise AIServiceProviderUnavailableError(
                    f"Service unavailable ({exc.status_code}): {exc}"
                ) from exc
            raise AIServiceError(
                f"API error ({exc.status_code}): {exc}"
            ) from exc
        except APIConnectionError as exc:
            raise AIServiceError(f"Connection error: {exc}") from exc
        except Exception as exc:
            raise AIServiceError(f"Unexpected error: {exc}") from exc
