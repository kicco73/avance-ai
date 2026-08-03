"""TurnStrategyV1: the original fallback — audio/signals/env travel
embedded as [audio]/[signals]/[env] tags inside the raw reply text itself
(see chat.metadata_handler.EMBED_METADATA_PROMPT), recovered here by tag-
filtering it (see chat.text_filter.ConcatTagFilter) rather than from any
live callback the provider itself makes. Used whenever the active
provider doesn't support on_metadata for the call shape this turn needs
(see ai.llm_provider.supports_structured_metadata/chat.turn_strategy_builder).
"""
from __future__ import annotations

import logging
from typing import Any

from ai.ai_service import OnRetry
from ai.llm_provider import AIServiceError
from chat.env import Env
from chat.metadata_handler import MetadataHandler
from chat.text_filter import ConcatTagFilter
from chat.turn_callbacks import OnChunk, OnMetadata
from chat.turn_strategy import TurnStrategy

logger = logging.getLogger(__name__)


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
        base_prompt: str,
        signal_definition: str | None,
        env: Env,
        chat_history: list[dict],
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> tuple[str, str | None, dict | None, dict]:
        # The classic tag-instructed metadata section (see
        # MetadataHandler.build_prompt/EMBED_METADATA_PROMPT) — appended
        # only when there's one to append at all (see TurnStrategy.
        # generate_reply's own docstring on signal_definition=None).
        system_prompt = (
            base_prompt if signal_definition is None
            else f"{base_prompt}\n\n{self._metadata_handler.build_prompt(signal_definition, env)}"
        )

        # ConcatTagFilter's own per-tag-kwarg convention (audio=...)
        # predates the unified on_metadata(key, value) callback every
        # caller now speaks (see OnMetadata's own docstring) — adapted
        # here, once, rather than teaching ConcatTagFilter a second
        # convention just for this.
        on_audio = (lambda value: on_metadata("audio", value)) if on_metadata is not None else None
        filter = ConcatTagFilter('audio', 'signals', 'env', audio=on_audio)

        # Always streamed internally now — every concrete provider has
        # real streaming support (see LLMProvider.generate's own shared
        # default, itself just "collect generate_stream()'s own chunks"),
        # so there's no separate blocking call left that would behave
        # any differently; on_chunk (given or not) only decides whether
        # each chunk is *also* forwarded live, not how the reply itself
        # is obtained.
        reply = ""
        async for chunk in self._ai_service.generate_stream(system_prompt, chat_history, on_retry=on_retry):
            chunk = filter.filter(chunk)
            reply += chunk
            if chunk and on_chunk is not None:
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
            if on_chunk is not None:
                await on_chunk(recovered)

        # Already the flat {name: value} dict to validate directly — the
        # [signals] tag's own content *is* that dictionary (see
        # MetadataHandler.EMBED_METADATA_PROMPT), no outer wrapper key
        # left to drill into.
        signal_values = self._metadata_handler._parse_metadata_tag(filter.tags['signals'].tag_content)
        env_updates = self._metadata_handler._parse_env_tag(filter.tags['env'].tag_content)
        audio_text = filter.tags['audio'].tag_content or None
        return reply, audio_text, signal_values, env_updates

    async def compute_explicitly(
        self, signal_definition: str, env: Env, call_history: list[dict],
    ) -> dict[str, Any]:
        """No reply to piggyback on — makes its own dedicated call, using
        the exact same system prompt/tag convention as generate_reply
        above, returning the raw {name: value} dict parsed straight off
        the [signals] tag, unvalidated (see tracking.evaluator.
        SignalEvaluator.validate, still the caller's own job)."""
        system_prompt = self._metadata_handler.build_prompt(signal_definition, env)
        try:
            raw_reply = await self._ai_service.generate(system_prompt, call_history)
        except AIServiceError as exc:
            logger.error("Failed to compute signals explicitly: %s", exc)
            return {}
        _, tags = self._metadata_handler._filter_text_and_extract_tags(raw_reply)
        return tags["signals"]
