"""TurnStrategyV2: a provider that supports on_metadata (see
ai.llm_provider.supports_on_metadata/gemini_provider_v2.py) reports
audio/signals/env directly, as each is resolved, instead of embedding
them as tags in the raw reply for TurnStrategyV1's own ConcatTagFilter to
strip back out afterward — so the text this returns is already exactly
what should be shown/saved, with nothing left to filter.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ai.ai_service import OnRetry
from chat.turn_callbacks import OnChunk, OnMetadata
from chat.turn_strategy import TurnStrategy


class TurnStrategyV2(TurnStrategy):
    async def generate_reply(
        self,
        system_prompt: str,
        chat_history: list[dict],
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> tuple[str, str | None, dict | None, dict]:
        captured: dict[str, Any] = {}

        def handle_metadata(key: str, value: Any) -> None:
            # Called sync, fire-and-forget, straight off the provider's
            # own call (see ai.llm_provider.MetadataCallback's own
            # docstring) — never awaited here. "audio" is the only key an
            # external caller (see ws_adapter.py's _push_metadata)
            # currently needs *live*, for immediate TTS playback —
            # signals/env are only ever needed once the turn is fully
            # done (see TurnProcessor._finish_turn), so those are just
            # captured, never forwarded live.
            captured[key] = value
            if key == "audio" and on_metadata is not None:
                asyncio.create_task(on_metadata(key, value))

        if on_chunk is not None:
            reply_parts: list[str] = []
            async for chunk in self._ai_service.generate_stream(
                system_prompt, chat_history, on_retry=on_retry, on_metadata=handle_metadata
            ):
                if chunk:
                    reply_parts.append(chunk)
                    await on_chunk(chunk)
            reply = "".join(reply_parts)
        else:
            reply = await self._ai_service.generate(
                system_prompt, chat_history, on_retry=on_retry, on_metadata=handle_metadata
            )

        audio_text = captured.get("audio")
        signal_values = captured.get("signals")
        env_updates = captured.get("env") or {}
        return reply, audio_text, signal_values, env_updates
