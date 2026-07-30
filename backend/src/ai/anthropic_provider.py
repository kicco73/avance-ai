"""LLM provider backed by the Anthropic API (Claude)."""
from __future__ import annotations

from http import HTTPStatus

import anthropic

from ai.llm_provider import (
    LLMProvider,
    AIServiceError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    AIServiceConfig,
)

CLAUDE_DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 30.0

CACHE_CONTROL = {"type": "ephemeral"}  # default 5-minute TTL is fine for this prototype


def _build_messages(history: list[dict]) -> list[dict]:
    """Translates provider-neutral history into Anthropic's shape: a list
    `content` (attachment blocks) becomes `document` blocks with a cache
    breakpoint on the last one; a plain string passes through untouched."""
    messages = []
    for message in history:
        content = message["content"]
        if not isinstance(content, list):
            messages.append({"role": message["role"], "content": content})
            continue

        blocks = [
            {"type": "document", "source": block["source"], "title": block["filename"]}
            for block in content
        ]
        blocks[-1] = {**blocks[-1], "cache_control": CACHE_CONTROL}
        messages.append({"role": message["role"], "content": blocks})
    return messages


class AnthropicProvider(LLMProvider):
    def __init__(self, config: AIServiceConfig) -> None:
        self._claude_model = config.model or CLAUDE_DEFAULT_MODEL
        self._client = anthropic.Anthropic(api_key=config.key, timeout=REQUEST_TIMEOUT_SECONDS)

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        try:
            response = self._client.messages.create(
                model=self._claude_model,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": system_prompt, "cache_control": CACHE_CONTROL}],
                messages=_build_messages(history),
            )
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

        text_parts = [block.text for block in response.content if block.type == "text"]
        return "".join(text_parts)
