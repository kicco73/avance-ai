from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from ai.llm_provider import MetadataCallback
from chat.env import Env
from chat.turn_protocol import TurnProtocol

logger = logging.getLogger(__name__)

EMBED_AUDIO_PROMPT = """
Definition of audio metadata:
    - a string designed for text-to-speech, not for reading.
    - Assume the user cannot see the screen at all.
    - Never refer to anything written on screen.
    - Use a nice, warm, human, non-robotic, constructive tone.
    - Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always fill in the 'audio' field of your structured response with the audio metadata value described above.
"""

EMBED_SIGNALS_PROMPT = """
Definition of signals metadata:
    - a string containing a JSON object, formatted as valid JSON text (e.g. "{\"mood\": 50.2}"),
     not a nested object.
    - it is vitally important to always calculate and return the value for each and any signal specified in the list below.
    - put all of the signals using their own name as the key and their value as the value.

Always fill in the 'signals' field of your structured response:
"""

EMBED_ENV_PROMPT = """
Definition of env metadata:
    - a persistent, cross-session memory of free-form facts about the
      user/conversation (e.g. preferences, ongoing goals) — distinct from
      signals, which are re-evaluated fresh every turn.

Always fill in the 'env' field of your structured response:
    - format is a string containing plain a "name: value" pair, one per line.
    - Only include a variable name when you are actually reporting something new or
      changed — omit the ones that haven't changed.
"""

class TurnProtocolUsingSchema(TurnProtocol):
    def generate_reply(
        self,
        base_prompt: str,
        signal_definition: str | None,
        env: Env,
        chat_history: list[dict],
        on_metadata: MetadataCallback,
    ) -> AsyncIterator[str]:

        system_prompt = self._build_prompt(base_prompt, signal_definition, env)
        print(system_prompt)
        return self._ai_service.generate_stream_with_metadata(
            system_prompt, chat_history, on_metadata=on_metadata
        )

    def _build_prompt(self, base_prompt: str, signal_definition: str | None, env: Env) -> str:
        env_block = "\n".join(f"{key}: {value}" for key, value in env.to_dict().items())
        return "\n\n".join([
            base_prompt,
            EMBED_AUDIO_PROMPT,
            EMBED_SIGNALS_PROMPT,
            signal_definition or "No signals are defined so far.",
            EMBED_ENV_PROMPT,
            f"Current env memory:\n{env_block}",
        ])

