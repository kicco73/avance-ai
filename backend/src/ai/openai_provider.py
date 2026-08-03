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
    LLMProvider,
    MetadataCallback,
    content_to_text,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: int = 1024


@contextmanager
def _handle_openai_errors() -> Generator[None, None, None]:
    """Context manager centralizzato per la gestione e rimappatura delle eccezioni OpenAI."""
    try:
        yield
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
    except Exception as exc:
        raise AIServiceError(f"Unhandled exception from the OpenAI API: {exc}") from exc


def _format_messages(
    system_prompt: str, history: list[dict[str, Any]]
) -> list[ChatCompletionMessageParam]:
    """Formats system prompt and history into OpenAI ChatCompletionMessageParam list."""
    messages: list[ChatCompletionMessageParam] = [
        cast(ChatCompletionMessageParam, {"role": "system", "content": system_prompt})
    ]

    for message in history:
        role: str = message["role"]
        if role in ("user", "assistant"):
            content_str = content_to_text(message["content"], "OpenAI")
            messages.append(
                cast(ChatCompletionMessageParam, {"role": role, "content": content_str})
            )

    return messages


class OpenAIProvider(LLMProvider):
    def __init__(self, config: AIServiceConfig) -> None:
        # Async only — generate() no longer has a separate blocking call
        # of its own (see LLMProvider.generate's own shared default,
        # built on top of generate_stream below), so there's nothing left
        # here that ever needs the sync client.
        self._async_client: AsyncOpenAI = AsyncOpenAI(api_key=config.key, base_url=config.url)
        self._model: str = config.model

    async def generate_stream(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        on_retry: OnRetry | None = None,
        on_metadata: MetadataCallback | None = None,
    ) -> AsyncIterator[str]:
        # on_metadata: unused — a "v1" provider never calls it (see
        # LLMProvider.generate_stream's own docstring for why it's still
        # accepted here regardless).
        messages: list[ChatCompletionMessageParam] = _format_messages(system_prompt, history)

        try:
            with _handle_openai_errors():
                response_stream = await self._async_client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    stream=True,
                )

            async for chunk in response_stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as exc:
            if isinstance(
                exc,
                (
                    AIServiceError,
                    AIServiceProviderRateLimitedError,
                    AIServiceProviderUnavailableError,
                ),
            ):
                raise exc
            with _handle_openai_errors():
                raise exc