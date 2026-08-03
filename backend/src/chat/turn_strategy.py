"""TurnStrategy: the one part of chat-turn processing that depends on
which "protocol" the active AI provider speaks (see ai.llm_provider.
supports_on_metadata) — isolated behind this shared interface so
ChatService can hold either concrete strategy (see turn_strategy_v1.py/
turn_strategy_v2.py) interchangeably, chosen fresh every turn by
chat.turn_strategy_builder.build_turn_strategy (a separate responsibility
on purpose — see its own module docstring). Everything else about
processing a turn (session/db bookkeeping, auto-tracking, building the
turn's own response) is genuinely identical either way and stays in
ChatService itself (see its own _begin_turn/_finish_turn) — only "how do
I actually get the reply text plus its audio/signals/env" differs, which
is exactly what generate_reply below covers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ai.ai_service import AiService, OnRetry
from chat.turn_callbacks import OnChunk, OnMetadata


class TurnStrategy(ABC):
    def __init__(self, ai_service: AiService) -> None:
        self._ai_service = ai_service

    @abstractmethod
    async def generate_reply(
        self,
        system_prompt: str,
        chat_history: list[dict],
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> tuple[str, str | None, dict | None, dict]:
        """Returns (reply, audio_text, signal_values, env_updates) for
        this one AI call — `chat_history` is already fully built (see
        TurnProcessor._generate_reply), so a concrete strategy only ever
        needs to decide *how* to ask the provider for a reply, streamed
        (on_chunk given) or not, and how to recover its own audio/signal-
        values/env alongside it. Never raises for a malformed reply — a
        missing/broken tag or metadata key just degrades to that piece
        being absent (None/{})."""
        raise NotImplementedError
