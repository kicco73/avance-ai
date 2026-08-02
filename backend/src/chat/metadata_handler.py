from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from chat.signals import Signals
from chat.text_filter import ConcatTagFilter

if TYPE_CHECKING:
    from chat.env import Env

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

Always add a [avance]...[/avance] tag at the end of every response.
    - Write the content inside it as a dictionary in JSON format.
        - put a key "signals" as a dictionary
            - put all of the using their name as the key and their value as the value.

Definition of env metadata:
    - a persistent, cross-session memory of free-form facts about the
      user/conversation (e.g. preferences, ongoing goals) — distinct from
      signals, which are re-evaluated fresh every turn.

Always add a [env]...[/env] tag at the end of every response, after
[avance]...[/avance]:
    - Write one "key: value" pair per line (optionally prefixed with "-").
    - Only include a key when you are actually reporting something new or
      changed — omit ones that haven't changed. Never invent values for
      the keys shown to you below — those are inputs supplied to you.
"""


class MetadataHandler(object):
    @staticmethod
    def signal_values(metadata: dict | None) -> dict | None:
        return (metadata or {}).get("signals")

    def _parse_metadata_tag(self, metadata_tag: str) -> Any:
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(metadata_tag) or {}
            assert isinstance(metadata, dict)
        except Exception as exc:
            logger.warning(f"_parse_metadata_tag(): {exc}")
        return metadata

    def _parse_env_tag(self, env_tag: str) -> dict[str, str]:
        """[env]...[/env]'s own content isn't JSON like [avance] — one
        "key: value" pair per line, each optionally prefixed with "-" (a
        bullet-list style the model sometimes prefers), blank lines and
        anything without a ':' ignored rather than raising. Not a
        strict-YAML parse: this format is deliberately more forgiving,
        since it's model output, not hand-authored config."""
        env: dict[str, str] = {}
        for line in (env_tag or "").splitlines():
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

    def _filter_text_and_extract_tags(self, text: str) -> tuple[str, dict]:
        filters = ConcatTagFilter('audio', 'avance', 'env')
        return filters.filter_and_flush(text), {
            'audio': filters.tags['audio'].tag_content,
            'signals': self._parse_metadata_tag(filters.tags['avance'].tag_content),
            'env': self._parse_env_tag(filters.tags['env'].tag_content),
        }

    def build_prompt(self, signals: Signals, env: "Env") -> str:
        env_block = "\n".join(f"{key}: {value}" for key, value in env.to_dict().items())
        return "\n".join([
            signals.get_definition(),
            EMBED_METADATA_PROMPT,
            f"[env]\n{env_block}\n[/env]",
        ])

