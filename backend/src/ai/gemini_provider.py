"""LLM provider backed by the Google Gemini API."""
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ai.llm_provider import (
    LLMProvider,
    AIServiceError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 1024

# Gemini uses the roles "user"/"model", not "user"/"assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}


def _content_to_text(content: Any) -> str:
    """Flattens provider-neutral attachment blocks to plain text (no
    `document` blocks for Gemini yet). Binary (base64) attachments are
    skipped, not supported here."""
    if isinstance(content, str):
        return content
    parts : list[str] = []
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
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

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
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except genai_errors.ClientError as exc:
            if exc.code == HTTPStatus.TOO_MANY_REQUESTS:
                raise AIServiceProviderRateLimitedError(
                    f"The Gemini API rate limit was exceeded (status 429): {exc.message}"
                ) from exc
            raise AIServiceError(
                f"Error from the Gemini API (status {exc.code}): {exc.message}"
            ) from exc
        except genai_errors.ServerError as exc:
            if exc.code == HTTPStatus.SERVICE_UNAVAILABLE:
                raise AIServiceProviderUnavailableError(
                    "The Gemini API is temporarily overloaded (status 503)."
                ) from exc
            raise AIServiceError(
                f"Error from the Gemini API (status {exc.code}). Please retry later."
            ) from exc
        except genai_errors.APIError as exc:
            raise AIServiceError(f"Unexpected error from the Gemini API: {exc}") from exc

        return response.text or ""
