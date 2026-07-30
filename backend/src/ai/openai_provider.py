"""LLM provider backed by OpenAI (or any OpenAI-compatible API like llama.cpp)."""
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import AsyncIterator

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncOpenAI,
    OpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam

from ai.llm_provider import (
    AIServiceConfig,
    AIServiceError,
    LLMProvider,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    content_to_text,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 1024


def _format_messages(
    system_prompt: str, history: list[dict]
) -> list[ChatCompletionMessageParam]:
    """Formats system prompt and history into OpenAI ChatCompletionMessageParam list."""
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt}
    ]

    for message in history:
        role = message["role"]
        if role == "user":
            messages.append({"role": "user", "content": content_to_text(message["content"], "OpenAI")})
        elif role == "assistant":
            messages.append({"role": "assistant", "content": content_to_text(message["content"], "OpenAI")})

    return messages


class OpenAIProvider(LLMProvider):
    def __init__(self, config: AIServiceConfig) -> None:
        self._client = OpenAI(api_key=config.key, base_url=config.url)
        self._async_client = AsyncOpenAI(api_key=config.key, base_url=config.url)
        self._model = config.model

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        messages = _format_messages(system_prompt, history)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        except RateLimitError as exc:
            raise AIServiceProviderRateLimitedError(
                f"The OpenAI API rate limit was exceeded (status 429): {exc.message}"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
                raise AIServiceProviderUnavailableError(
                    "The AI service is temporarily overloaded (status 503)."
                ) from exc
            raise AIServiceError(
                f"Error from the OpenAI API (status {exc.status_code}): {exc.message}"
            ) from exc
        except APIConnectionError as exc:
            raise AIServiceProviderUnavailableError(
                f"Could not connect to the AI service endpoint: {exc.message}"
            ) from exc
        except APIError as exc:
            raise AIServiceError(f"Unexpected error from the OpenAI API: {exc}") from exc

        return response.choices[0].message.content or ""

    async def generate_stream(
        self, system_prompt: str, history: list[dict]
    ) -> AsyncIterator[str]:
        messages = _format_messages(system_prompt, history)

        try:
            response_stream = await self._async_client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                stream=True,
            )

            async for chunk in response_stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except RateLimitError as exc:
            raise AIServiceProviderRateLimitedError(
                f"The OpenAI API rate limit was exceeded (status 429): {exc.message}"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
                raise AIServiceProviderUnavailableError(
                    "The AI service is temporarily overloaded (status 503)."
                ) from exc
            raise AIServiceError(
                f"Error from the OpenAI API (status {exc.status_code}): {exc.message}"
            ) from exc
        except APIConnectionError as exc:
            raise AIServiceProviderUnavailableError(
                f"Could not connect to the AI service endpoint: {exc.message}"
            ) from exc
        except APIError as exc:
            raise AIServiceError(f"Unexpected error from the OpenAI API: {exc}") from exc