"""LLM provider backed by the Anthropic API (Claude)."""
from __future__ import annotations

import os

import anthropic

from llm_provider import LLMProvider, LLMProviderError, LLMProviderUnavailableError

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 30.0


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
                system=system_prompt,
                messages=history,
            )
        except anthropic.APITimeoutError as exc:
            raise LLMProviderError("Timeout while calling the model. Please retry.") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 503:
                raise LLMProviderUnavailableError(
                    "The Anthropic API is temporarily overloaded (status 503)."
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
