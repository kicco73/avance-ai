"""LLM provider backed by Google Gemini API."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, AsyncIterator, Generator
import logging

from google import genai
from google.genai import types
from google.genai.errors import APIError

from ai.llm_provider import (
    AIServiceConfig,
    AIServiceError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
    LLMProvider,
    content_to_text,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: int = 1024


@contextmanager
def _handle_gemini_errors() -> Generator[None, None, None]:
    """Context manager centralizzato per la gestione e rimappatura delle eccezioni dell'SDK Google GenAI."""
    try:
        yield
    except APIError as exc:
        code: int | None = getattr(exc, "code", None)
        message: str = getattr(exc, "message", str(exc))

        if code == 429:
            raise AIServiceProviderRateLimitedError(
                f"The Gemini API rate limit was exceeded (status 429): {message}"
            ) from exc
        if code in (503, 504):
            raise AIServiceProviderUnavailableError(
                f"The Gemini service is temporarily overloaded (status {code}): {message}"
            ) from exc
        raise AIServiceError(
            f"Error from the Gemini API (status {code}): {message}"
        ) from exc
    except Exception as exc:
        raise AIServiceError(f"Unexpected error from the Gemini API: {exc}") from exc


class GeminiProvider(LLMProvider):
    def __init__(self, config: AIServiceConfig) -> None:
        self._client: genai.Client = genai.Client(
            api_key=config.key,
            http_options={"base_url": config.url} if config.url else None,
        )
        self._model_name: str = config.model

    def _format_history_and_config(
        self, system_prompt: str, history: list[dict[str, Any]]
    ) -> tuple[list[types.Content], types.GenerateContentConfig]:
        contents: list[types.Content] = []

        for message in history:
            role: str = "model" if message["role"] == "assistant" else "user"
            text_content: str = content_to_text(message["content"], "Gemini")

            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=text_content)],
                )
            )

        gen_config: types.GenerateContentConfig = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        return contents, gen_config

    def generate(self, system_prompt: str, history: list[dict[str, Any]]) -> str:
        contents, config = self._format_history_and_config(system_prompt, history)

        with _handle_gemini_errors():
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=config,
            )
            return response.text or ""

    async def generate_stream(
        self, system_prompt: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[str]:
        contents, config = self._format_history_and_config(system_prompt, history)

        with _handle_gemini_errors():
            response_stream = await self._client.aio.models.generate_content_stream(
                model=self._model_name,
                contents=contents,
                config=config,
            )

            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text