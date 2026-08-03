"""TurnStrategyV1: the original fallback — audio/signals/env travel
embedded as [audio]/[signals]/[env] tags inside the raw reply text itself
(see chat.metadata_handler.EMBED_METADATA_PROMPT), recovered here by tag-
filtering it (see chat.text_filter.ConcatTagFilter) rather than from any
live callback the provider itself makes. Used whenever the active
provider doesn't support on_metadata for the call shape this turn needs
(see ai.llm_provider.supports_on_metadata/chat.turn_strategy_builder).
"""
from __future__ import annotations

from ai.ai_service import OnRetry
from chat.metadata_handler import MetadataHandler
from chat.text_filter import ConcatTagFilter
from chat.turn_callbacks import OnChunk, OnMetadata
from chat.turn_strategy import TurnStrategy


class TurnStrategyV1(TurnStrategy):
    def __init__(self, ai_service) -> None:
        super().__init__(ai_service)
        # Stateless per call (every _parse_*/build_prompt method reads
        # only its own arguments) — a private instance here rather than
        # one shared with ChatService's own (see chat_service.py's
        # _build_turn_prompt/_generate_opening_message_body, which use it
        # for unrelated turns) keeps this strategy self-contained.
        self._metadata_handler = MetadataHandler()

    async def generate_reply(
        self,
        system_prompt: str,
        chat_history: list[dict],
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> tuple[str, str | None, dict | None, dict]:
        # ConcatTagFilter's own per-tag-kwarg convention (audio=...)
        # predates the unified on_metadata(key, value) callback every
        # caller now speaks (see OnMetadata's own docstring) — adapted
        # here, once, rather than teaching ConcatTagFilter a second
        # convention just for this.
        on_audio = (lambda value: on_metadata("audio", value)) if on_metadata is not None else None
        filter = ConcatTagFilter('audio', 'signals', 'env', audio=on_audio)

        if on_chunk is not None:
            reply = await self._receive_ai_stream_and_sendreply(system_prompt, chat_history, filter, on_chunk)
        else:
            reply = await self._ai_service.generate(system_prompt, chat_history, on_retry=on_retry)
            reply = filter.filter_and_flush(reply)

        # Already the flat {name: value} dict to validate directly — the
        # [signals] tag's own content *is* that dictionary (see
        # MetadataHandler.EMBED_METADATA_PROMPT), no outer wrapper key
        # left to drill into.
        signal_values = self._metadata_handler._parse_metadata_tag(filter.tags['signals'].tag_content)
        env_updates = self._metadata_handler._parse_env_tag(filter.tags['env'].tag_content)
        audio_text = filter.tags['audio'].tag_content or None
        return reply, audio_text, signal_values, env_updates

    async def _receive_ai_stream_and_sendreply(self, system_prompt: str, chat_history, filter, on_chunk) -> str:
        reply = ""
        async for chunk in self._ai_service.generate_stream(system_prompt, chat_history):
            chunk = filter.filter(chunk)
            reply += chunk
            if chunk:
                await on_chunk(chunk)
        # The stream has truly ended — recover anything still stuck
        # behind a tag the model opened but never closed (see
        # ConcatTagFilter.flush's own docstring: a real failure mode,
        # not hypothetical — otherwise the rest of the reply is silently
        # lost and the user sees an empty bubble). filter() alone, above,
        # never does this on its own since more of an in-progress tag
        # could always still be in the next chunk.
        recovered = filter.flush()
        if recovered:
            reply += recovered
            await on_chunk(recovered)
        return reply
