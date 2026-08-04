from __future__ import annotations

import logging
from typing import AsyncIterator

from ai.llm_provider import MetadataCallback
from chat.env import Env
from chat.text_filter import ConcatTagFilter
from chat.turn_protocol import TurnProtocol

logger = logging.getLogger(__name__)

EMBED_METADATA_PROMPT = """
Definition of audio metadata:
    - a string designed for text-to-speech, not for reading.
    - Assume the user cannot see the screen at all.
    - Never refer to anything written on screen.
    - Use a nice, warm, human, non-robotic, constructive tone.
    - Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always add a [audio]...[/audio] tag at the very beginning of every response:
    - put the audio metadata value between the markups.

Always add a [signals]...[/signals] tag at the end of every response.
    - Write the content inside it as a dictionary in JSON format.
        - put all of the signals using their name as the key and their value as the value.

Definition of env metadata:
    - a persistent, cross-session memory of free-form facts about the
      user/conversation (e.g. preferences, ongoing goals) — distinct from
      signals, which are re-evaluated fresh every turn.

Always add a [env]...[/env] tag at the end of every response:
    - Write one "key: value" pair per line (optionally prefixed with "-").
    - Never invent values for the keys shown to you below — those are inputs supplied to you.
"""
class TurnProcotolUsingTextExtraction(TurnProtocol):

    async def generate_reply(
        self,
        base_prompt: str,
        signal_definition: str | None,
        env: Env,
        chat_history: list[dict],
        on_metadata: MetadataCallback,
    ) -> AsyncIterator[str]:
        system_prompt = self._build_prompt(base_prompt, signal_definition, env)

        tags = ('audio', 'signals', 'env')
        metadata_handlers = {tag: lambda value: on_metadata(tag, value) for tag in tags}
        filter = ConcatTagFilter(*tags, **metadata_handlers)

        async for chunk in self._ai_service.generate_stream(system_prompt, chat_history):
            chunk = filter.filter(chunk)
            yield chunk

        recovered = filter.flush()
        if recovered:
            yield recovered

    def _build_prompt(self, base_prompt: str, signal_definition: str | None, env: Env) -> str:
        env_block = "\n".join(f"{key}: {value}" for key, value in env.to_dict().items())
        return "\n\n".join([
            base_prompt,
            signal_definition or "No signals are defined so far.",
            EMBED_METADATA_PROMPT,
            f"[env]\n{env_block}\n[/env]",
        ])

