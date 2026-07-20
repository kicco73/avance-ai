"""LLM provider backed by the Google Gemini API."""
from __future__ import annotations

import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMProviderRateLimitedError,
    LLMProviderUnavailableError,
)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
MAX_OUTPUT_TOKENS = 1024

# Gemini uses the roles "user"/"model", not "user"/"assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}


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
                "parts": [{"text": message["content"]}],
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
