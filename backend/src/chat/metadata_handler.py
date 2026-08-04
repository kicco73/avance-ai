from __future__ import annotations

import json
import logging
from typing import Any

from chat.text_filter import ConcatTagFilter

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

Always add a [env]...[/env] tag at the end of every response, after
[signals]...[/signals]:
    - Write one "key: value" pair per line (optionally prefixed with "-").
    - Never invent values for the keys shown to you below — those are inputs supplied to you.
"""
class MetadataHandler(object):
    @staticmethod
    def parse_raw_signals(raw_signals: str) -> dict[str,float]:
        signals: dict[str, Any] = {}
        if not raw_signals:
            return signals
        try:
            signals = json.loads(raw_signals) or {}
            assert isinstance(signals, dict)
        except Exception as exc:
            logger.warning(f"parse_raw_signals(): {exc}")
        return signals

    @staticmethod
    def parse_raw_env(raw_env: str) -> dict[str, str]:
        """Env`own content isn't JSON like [signals] — one
        "key: value" pair per line, each optionally prefixed with "-" (a
        bullet-list style the model sometimes prefers), blank lines and
        anything without a ':' ignored rather than raising. Not a
        strict-YAML parse: this format is deliberately more forgiving,
        since it's model output, not hand-authored config."""
        env: dict[str, str] = {}
        for line in (raw_env or "").splitlines():
            line = line.strip()
            if line.startswith("-"):
                line = line[1:].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            if key:
                env[key] = value.strip()
        return env

    def parse(self, key: str, value: str) -> str | dict[str, float | str]:
        parser = {
            'signals': self.parse_raw_signals,
            'env': self.parse_raw_env,
        }
        return parser.get(key, lambda x: x)(value)

    def _filter_text_and_extract_tags(self, text: str) -> tuple[str, dict]:
        filters = ConcatTagFilter('audio', 'signals', 'env')
        return filters.filter_and_flush(text), {
            'audio': filters.tags['audio'].tag_content,
            'signals': self.parse_raw_signals(filters.tags['signals'].tag_content),
            'env': self.parse_raw_env(filters.tags['env'].tag_content),
        }
