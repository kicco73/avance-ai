"""LLM provider backed by the Google Gemini API."""
from __future__ import annotations

import logging
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ai.llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMProviderRateLimitedError,
    LLMProviderUnavailableError,
)

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
MAX_OUTPUT_TOKENS = 1024

# Gemini uses the roles "user"/"model", not "user"/"assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}


def _content_to_text(content) -> str:
    """No prompt caching or `document` content blocks for Gemini in this
    version — main.py's provider-neutral 'attachment' blocks (see
    _build_priming_messages) are flattened into plain text instead, a
    reasonable fallback while every attachment is text. A PDF attachment
    (source type "base64") can't be represented as text and is skipped here:
    supporting it would mean building Gemini's `inline_data` parts with the
    raw base64 bytes, which is out of scope for now."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        source = block["source"]
        if source["type"] == "text":
            parts.append(f"[Attachment: {block['filename']}]\n{source['data']}")
        else:
            logger.warning(
                "Skipping unsupported binary attachment '%s' for Gemini (no document-block support yet).",
                block["filename"],
            )
    return "\n\n".join(parts)


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMProviderError(
                "GEMINI_API_KEY not configured. Copy .env.example to .env and enter your API key."
            )
        self._client = genai.Client(api_key=api_key)

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        contents = [
            {
                "role": _ROLE_MAP[message["role"]],
                "parts": [{"text": _content_to_text(message["content"])}],
            }
            for message in history
        ]

        try:
            response = self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise LLMProviderRateLimitedError(
                    f"The Gemini API rate limit was exceeded (status 429): {exc.message}"
                ) from exc
            raise LLMProviderError(
                f"Error from the Gemini API (status {exc.code}): {exc.message}"
            ) from exc
        except genai_errors.ServerError as exc:
            if exc.code == 503:
                raise LLMProviderUnavailableError(
                    "The Gemini API is temporarily overloaded (status 503)."
                ) from exc
            raise LLMProviderError(
                f"Error from the Gemini API (status {exc.code}). Please retry later."
            ) from exc
        except genai_errors.APIError as exc:
            raise LLMProviderError(f"Unexpected error from the Gemini API: {exc}") from exc

        return response.text or ""
