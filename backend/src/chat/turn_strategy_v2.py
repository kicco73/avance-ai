"""TurnStrategyV2: a provider that supports on_metadata (see
ai.llm_provider.supports_structured_metadata/gemini_provider_v2.py) reports
audio/signals/env directly, as each is resolved, instead of embedding
them as tags in the raw reply for TurnStrategyV1's own ConcatTagFilter to
strip back out afterward — so the text this returns is already exactly
what should be shown/saved, with nothing left to filter.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ai.ai_service import OnRetry
from ai.llm_provider import AIServiceError
from chat.env import Env
from chat.turn_callbacks import OnChunk, OnMetadata
from chat.turn_strategy import TurnStrategy

logger = logging.getLogger(__name__)

metadata_instructions = """
Definition of audio metadata:
    - a string designed for text-to-speech, not for reading.
    - Assume the user cannot see the screen at all.
    - Never refer to anything written on screen.
    - Use a nice, warm, human, non-robotic, constructive tone.
    - Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always fill in the 'audio' field of your structured response with the audio metadata value described above.

Definition of signals metadata:
    - a string containing a JSON object, formatted as valid JSON text (e.g. "{\"mood\": 50.2}"), not a nested object.
    - put all of the signals using their own name as the key and their value as the value.

Always fill in the 'signals' field of your structured response:

Definition of env metadata:
    - a persistent, cross-session memory of free-form facts about the
      user/conversation (e.g. preferences, ongoing goals) — distinct from
      signals, which are re-evaluated fresh every turn.

Always fill in the 'env' field of your structured response:
    - format is a string containing a JSON object of "key": "value" pairs, formatted as valid JSON text, not a nested object.
    - Only include a key when you are actually reporting something new or
      changed — omit the ones that haven't changed.
"""


def _coerce_metadata_value(key: str, value: Any) -> Any:
    """Every non-'audio' field of gemini_provider_v2.py's own schema is
    typed STRING regardless of its real shape (see GeminiProvider.
    build_schema's own docstring) — the model writes "signals"/"env" as
    JSON-formatted text inside that string, not a native nested object,
    so this is what actually recovers the dict a caller needs. A
    non-string value is returned unchanged (nothing to parse); a
    malformed one logs instead of silently losing the error, degrading
    to {} same as a missing/empty report would."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        logger.warning("Could not JSON-parse '%s' metadata value: %r", key, value)
        return {}


class TurnStrategyV2(TurnStrategy):
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
        # Unlike TurnStrategyV1's own metadata section, this never asks
        # for [audio]/[signals]/[env] tags — gemini_provider_v2.py's own
        # _format_history_and_config already forces a structured JSON
        # schema with its own "audio"/"text"/"signals"/"env" fields on
        # every call; appending the tag-based instructions on top would
        # just give the model two conflicting formats to satisfy at once
        # (see this session's own integration-test finding: a real model
        # tried to honor both, embedding literal [audio]/[signals]/[env]
        # markup *inside* the JSON "text" field itself).
        system_prompt = (
            base_prompt if signal_definition is None
            else f"{base_prompt}\n\n{self._build_metadata_prompt(signal_definition, env)}"
        )

        captured: dict[str, Any] = {}

        def handle_metadata(key: str, value: Any) -> None:
            if key == "audio":
                if on_metadata is not None:
                    asyncio.create_task(on_metadata(key, value))
            else:
                value = _coerce_metadata_value(key, value)
            captured[key] = value

        reply_parts: list[str] = []
        async for chunk in self._ai_service.generate_stream(
            system_prompt, chat_history, on_retry=on_retry, on_metadata=handle_metadata
        ):
            if chunk:
                reply_parts.append(chunk)
                if on_chunk is not None:
                    await on_chunk(chunk)
        reply = "".join(reply_parts)

        audio_text = captured.get("audio")
        signal_values = captured.get("signals")
        env_updates = captured.get("env") or {}
        return reply, audio_text, signal_values, env_updates

    async def compute_explicitly(
        self, signal_definition: str, env: Env, call_history: list[dict],
    ) -> dict[str, Any]:
        """No reply to piggyback on — makes its own dedicated call, using
        the exact same system prompt/on_metadata convention as
        generate_reply above, returning the raw {name: value} dict
        recovered off the 'signals' field, unvalidated (see tracking.
        evaluator.SignalEvaluator.validate, still the caller's own job).
        "audio"/"env", also reported alongside it, are irrelevant here
        and simply never captured."""
        system_prompt = self._build_metadata_prompt(signal_definition, env)
        captured: dict[str, Any] = {}

        def handle_metadata(key: str, value: Any) -> None:
            if key == "signals":
                captured["signals"] = _coerce_metadata_value(key, value)

        try:
            await self._ai_service.generate(system_prompt, call_history, on_metadata=handle_metadata)
        except AIServiceError as exc:
            logger.error("Failed to compute signals explicitly: %s", exc)
            return {}
        return captured.get("signals") or {}

    @staticmethod
    def _build_metadata_prompt(signal_definition: str, env: Env) -> str:
        env_block = "\n".join(f"{key}: {value}" for key, value in env.to_dict().items())

        return "\n\n".join([signal_definition, metadata_instructions, f"Current env memory:\n{env_block}"])
