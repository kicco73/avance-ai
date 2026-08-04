from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from ai.ai_service import AiService
from ai.llm_provider import MetadataCallback
from chat.env import Env

class TurnProtocol(ABC):
    def __init__(self, ai_service: AiService) -> None:
        self._ai_service = ai_service

    @abstractmethod
    def generate_reply(
        self,
        base_prompt: str,
        signal_definition: str | None,
        env: Env,
        chat_history: list[dict],
        on_metadata: MetadataCallback,
    ) -> AsyncIterator[str]:
        """Returns chunks of text coming from the response streaming and
        calls metadata callback to handle tags in a compatible way for V1 and V2.
        """
        raise NotImplementedError

    @abstractmethod
    def _build_prompt(self, base_prompt: str, signal_definition: str | None, env: Env) -> str:
        pass
