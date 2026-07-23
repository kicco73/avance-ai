"""LLM provider backed by the Anthropic API (Claude)."""
from __future__ import annotations

import os

import anthropic

from ai.llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMProviderRateLimitedError,
    LLMProviderUnavailableError,
)

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 30.0

CACHE_CONTROL = {"type": "ephemeral"}  # default 5-minute TTL is fine for this prototype


def _build_messages(history: list[dict]) -> list[dict]:
    """Translates main.py's provider-neutral history into Anthropic's shape:
    a plain-string `content` passes through untouched; a list `content`
    (main.py's _build_priming_messages — attachment blocks) becomes real
    `document` content blocks, with a cache breakpoint on the last one. At
    most one such message exists per call (always first), so the breakpoint
    is naturally scoped to "this call's attachments" — stable turn to turn
    only as long as the current state's (or signal's) attachment set is."""
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
    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMProviderError(
                "ANTHROPIC_API_KEY not configured. Copy .env.example to .env and enter your API key."
            )
        self._client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        try:
            response = self._client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": system_prompt, "cache_control": CACHE_CONTROL}],
                messages=_build_messages(history),
            )
        except anthropic.APITimeoutError as exc:
            raise LLMProviderError("Timeout while calling the model. Please retry.") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 503:
                raise LLMProviderUnavailableError(
                    "The Anthropic API is temporarily overloaded (status 503)."
                ) from exc
            if exc.status_code == 429:
                raise LLMProviderRateLimitedError(
                    "The Anthropic API rate limit was exceeded (status 429)."
                ) from exc
            raise LLMProviderError(
                f"Error from the Anthropic API (status {exc.status_code}). Please retry later."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(
                "Unable to reach the Anthropic API. Check your network connection."
            ) from exc
        except anthropic.APIError as exc:
            raise LLMProviderError(f"Unexpected error from the Anthropic API: {exc}") from exc

        text_parts = [block.text for block in response.content if block.type == "text"]
        return "".join(text_parts)
