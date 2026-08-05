from __future__ import annotations

import logging
from typing import AsyncIterator

from ai.llm_provider import MetadataCallback
from chat.text_filter import ConcatTagFilter
from chat.turn_protocol import TurnProtocol

logger = logging.getLogger(__name__)

EMBED_AUDIO_TAG_PROMPT = """
Definition of audio metadata:
    - a string designed for text-to-speech, not for reading.
    - Assume the user cannot see the screen at all.
    - Never refer to anything written on screen.
    - Use a nice, warm, human, non-robotic, constructive tone.
    - Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always add a [audio]...[/audio] tag at the very beginning of every response:
    - put the audio metadata value between the markups.
"""

EMBED_SIGNAL_TAG_PROMPT = """"
Always add a [signals]...[/signals] tag at the end of every response.
    - Write the content inside it as a dictionary in JSON format.
        - put all of the signals using their name as the key and their value as the value.
"""

EMBED_ENV_TAG_PROMPT = """"     
Definition of env metadata:
    - a persistent, cross-session memory of free-form facts about the
      user/conversation (e.g. preferences, ongoing goals) — distinct from
      signals, which are re-evaluated fresh every turn.

Always add a [env]...[/env] tag at the end of every response:
    - Write one "key: value" pair per line (optionally prefixed with "-").
    - Never invent values for the keys shown to you below — those are inputs supplied to you.
"""
class TurnProcotolUsingTextExtraction(TurnProtocol):

    prompt_preambles = {
        'env': EMBED_ENV_TAG_PROMPT,
        'audio': EMBED_AUDIO_TAG_PROMPT,
        'signals': EMBED_SIGNAL_TAG_PROMPT,
        'text': ''
    }

    async def _generate_reply(self, prompt: str, chat_history: list[dict], on_metadata: MetadataCallback,) -> AsyncIterator[str]:

        metadata_handlers = {tag: lambda value: on_metadata(tag, value) for tag in self.include_tags}
        filter = ConcatTagFilter(*self.include_tags, **metadata_handlers)

        async for chunk in self._ai_service.generate_stream(prompt, chat_history):
            chunk = filter.filter(chunk)
            yield chunk

        recovered = filter.flush()
        if recovered:
            yield recovered

