"""LLM provider backed by the Anthropic API (Claude)."""
from __future__ import annotations

from contextlib import contextmanager
from http import HTTPStatus
from typing import Any, AsyncIterator, Generator, cast

import anthropic
from anthropic.types import CacheControlEphemeralParam, MessageParam, TextBlockParam

from cascade import OnRetry
from ai.llm_provider import (
    AIServiceConfig,
    AIServiceError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    LLMProvider,
)

CLAUDE_DEFAULT_MODEL: str = "claude-sonnet-5"
MAX_TOKENS: int = 1024
REQUEST_TIMEOUT_SECONDS: float = 30.0

CACHE_CONTROL: CacheControlEphemeralParam = {"type": "ephemeral"}


@contextmanager
def _handle_anthropic_errors() -> Generator[None, None, None]:
    """Context manager centralizzato per la gestione e rimappatura delle eccezioni Anthropic."""
    try:
        yield
    except anthropic.APITimeoutError as exc:
        raise AIServiceError("Timeout while calling the model. Please retry.") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
            raise AIServiceProviderUnavailableError(
                "The Anthropic API is temporarily overloaded (status 503)."
            ) from exc
        if exc.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.TOO_MANY_REQUESTS):
            raise AIServiceProviderRateLimitedError(
                "The Anthropic API rate limit was exceeded (status 429)."
            ) from exc
        raise AIServiceError(
            f"Error from the Anthropic API (status {exc.status_code}). Please retry later."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise AIServiceError(
            "Unable to reach the Anthropic API. Check your network connection."
        ) from exc
    except anthropic.APIError as exc:
        raise AIServiceError(f"Unexpected error from the Anthropic API: {exc}") from exc
    except Exception as exc:
        raise AIServiceError(f"Unhandled exception from the Anthropic API: {exc}") from exc


def _build_messages(history: list[dict[str, Any]]) -> list[MessageParam]:
    """Translates provider-neutral history into Anthropic's shape: a list
    `content` (attachment blocks) becomes `document` blocks with a cache
    breakpoint on the last one; a plain string passes through untouched."""
    messages: list[MessageParam] = []
    for message in history:
        content: Any = message["content"]
        role: Any = message["role"]

        if not isinstance(content, list):
            messages.append(cast(MessageParam, {"role": role, "content": content}))
            continue

        blocks: list[dict[str, Any]] = [
            {"type": "document", "source": block["source"], "title": block["filename"]}
            for block in content
        ]
        blocks[-1] = {**blocks[-1], "cache_control": CACHE_CONTROL}
        messages.append(cast(MessageParam, {"role": role, "content": blocks}))
    return messages


class AnthropicProvider(LLMProvider):
    def __init__(self, config: AIServiceConfig) -> None:
        self._claude_model: str = config.model or CLAUDE_DEFAULT_MODEL
        self._client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=config.key, timeout=REQUEST_TIMEOUT_SECONDS
        )
        self._async_client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(
            api_key=config.key, timeout=REQUEST_TIMEOUT_SECONDS
        )

    def generate(
        self, system_prompt: str, history: list[dict[str, Any]], on_retry: OnRetry | None = None
    ) -> str:
        # on_retry: unused — a leaf provider never retries on its own (see LLMProvider.generate).
        system_blocks: list[TextBlockParam] = [
            {"type": "text", "text": system_prompt, "cache_control": CACHE_CONTROL}
        ]
        with _handle_anthropic_errors():
            response = self._client.messages.create(
                model=self._claude_model,
                max_tokens=MAX_TOKENS,
                system=system_blocks,
                messages=_build_messages(history),
            )
            text_parts: list[str] = [
                block.text for block in response.content if block.type == "text"
            ]
            return "".join(text_parts)

    async def generate_stream(
        self, system_prompt: str, history: list[dict[str, Any]], on_retry: OnRetry | None = None
    ) -> AsyncIterator[str]:
        system_blocks: list[TextBlockParam] = [
            {"type": "text", "text": system_prompt, "cache_control": CACHE_CONTROL}
        ]
        
        try:
            with _handle_anthropic_errors():
                stream_manager = self._async_client.messages.stream(
                    model=self._claude_model,
                    max_tokens=MAX_TOKENS,
                    system=system_blocks,
                    messages=_build_messages(history),
                )
            
            async with stream_manager as stream:
                async for text in stream.text_stream:
                    yield text

        except Exception as exc:
            # Cattura eventuali eccezioni sollevate durante l'iterazione dello stream
            if isinstance(
                exc,
                (
                    AIServiceError,
                    AIServiceProviderRateLimitedError,
                    AIServiceProviderUnavailableError,
                ),
            ):
                raise exc
            with _handle_anthropic_errors():
                raise exc