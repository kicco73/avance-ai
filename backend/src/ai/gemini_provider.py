"""LLM provider backed by Google Gemini API."""
from __future__ import annotations

import logging
from typing import AsyncIterator

from ai.llm_provider import (
    AIServiceConfig,
    LLMProvider,
    content_to_text,
)

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    def __init__(self, config: AIServiceConfig) -> None:
        # Inizializzazione SDK Gemini
        self._model_name = config.model
        # ...

    def _format_history(self, history: list[dict]) -> list[dict]:
        formatted = []
        for message in history:
            formatted.append({
                "role": message["role"],
                "parts": [content_to_text(message["content"], "Gemini")]
            })
        return formatted

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        # ... logic implementation
        raise NotImplementedError

    async def generate_stream(
        self, system_prompt: str, history: list[dict]
    ) -> AsyncIterator[str]:
        # ... logic implementation
        raise NotImplementedError
        yield ""