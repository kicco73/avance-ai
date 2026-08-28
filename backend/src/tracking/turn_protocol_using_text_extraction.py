from __future__ import annotations

from typing import AsyncIterator

from ai.llm_provider import MetadataCallback
from logging_factory import LoggerFactory
from tracking.tag_prompt_builder import TagPromptBuilder
from tracking.text_filter import ConcatTagFilter
from tracking.turn_protocol import TurnProtocol

logger = LoggerFactory.get_logger(__name__)

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

# Batch-only variants (BatchSignalSource, covering several turns in one
# call) — a single live turn or turn-by-turn replay uses the plain
# EMBED_SIGNAL_TAG_PROMPT/EMBED_ENV_TAG_PROMPT above instead, since it has
# no turn-numbering concept at all to get wrong. Keeping the two totally
# separate (rather than one prompt trying to describe both shapes) is
# deliberate — the shared version proved unstable across single-turn
# calls (extra rows, wrong turn numbers, missing turn-number prefix).
EMBED_SIGNAL_BATCH_TAG_PROMPT = """"
Always add a [signals]...[/signals] tag at the end of every response.
    - Write the content inside it as a small CSV table, as plain text (not a JSON object).
    - First row: the signal names, comma-separated, e.g. "mood,engagement".
    - One data row per turn, each starting with that turn's own number — the same
      number shown on its "[Turn N/Total]" marker in the conversation transcript —
      followed by that turn's values. The transcript's turn numbers always run
      1, 2, 3, ... with no gaps, so with 3 marked turns you write exactly 3 rows:
      mood,engagement
      1,50.2,70
      2,52.0,68
      3,60.0,75
    - it is vitally important to always calculate and return a value for each and any
      signal specified in the list below, for every turn marked in the transcript —
      never skip one, never merge two into one row.
"""

EMBED_ENV_BATCH_TAG_PROMPT = """"
Definition of env metadata:
    - a persistent, cross-session memory of free-form facts about the
      user/conversation (e.g. preferences, ongoing goals) — distinct from
      signals, which are re-evaluated fresh every turn.

Always add a [env]...[/env] tag at the end of every response:
    - Write a JSON object, as plain text, keyed by each turn's own number — the
      same number shown on its "[Turn N/Total]" marker in the conversation transcript.
      The transcript's turn numbers always run 1, 2, 3, ... with no gaps, so with
      3 marked turns you write exactly 3 entries:
      {"1": {"favorite_color": "blue"}, "2": {}, "3": {"mood": "better"}}
    - Each entry's value is an object of only the variable names you are actually
      reporting as new or changed that turn — map it to {} when nothing changed.
    - One entry per turn marked in the transcript — never skip one, never merge
      two into one entry.
    - Never invent values for the keys shown to you below — those are inputs supplied to you.
"""

EMBED_REACTION_TAG_PROMPT = """"
Definition of reaction metadata:
    - the key of one reaction from the project's own declared reaction
      vocabulary, chosen to react to the user's last message.
    - leave it empty when no declared reaction fits this turn.

Always add a [reaction]...[/reaction] tag at the end of every response:
    - put the chosen reaction key between the markups, or leave it empty.
"""
class TurnProcotolUsingTextExtraction(TurnProtocol):

    prompt_preambles = {
        'env': EMBED_ENV_TAG_PROMPT,
        'audio': EMBED_AUDIO_TAG_PROMPT,
        'signals': EMBED_SIGNAL_TAG_PROMPT,
        'reaction': EMBED_REACTION_TAG_PROMPT,
        'text': '',
        'signals_batch': EMBED_SIGNAL_BATCH_TAG_PROMPT,
        'env_batch': EMBED_ENV_BATCH_TAG_PROMPT,
    }

    def _generate_reply(self, prompt: str, chat_history: list[dict], on_metadata: MetadataCallback,) -> AsyncIterator[str]:
        return self._stream_and_filter(prompt, chat_history, list(self.include_tags), on_metadata)

    def generate_reply_with_schema(
        self, base_prompt: str, tag_specs: list[tuple[str, str]], chat_history: list[dict], on_metadata: MetadataCallback,
    ) -> AsyncIterator[str]:
        preambles = TagPromptBuilder().build(tag_specs, self.prompt_preambles)
        tag_names = [tag for tag, _ in tag_specs]

        content = [preambles[tag] for tag in tag_names] + [base_prompt]
        prompt = "\n\n".join(content)

        return self._stream_and_filter(prompt, chat_history, tag_names, on_metadata)

    async def _stream_and_filter(
        self, prompt: str, chat_history: list[dict], tag_names: list[str], on_metadata: MetadataCallback,
    ) -> AsyncIterator[str]:
        metadata_handlers = {tag: lambda value, tag=tag: on_metadata(tag, value) for tag in tag_names}
        filter = ConcatTagFilter(*tag_names, **metadata_handlers)

        async for chunk in self._ai_service.generate_stream(prompt, chat_history):
            chunk = filter.filter(chunk)
            yield chunk

        recovered = filter.flush()
        if recovered:
            yield recovered

