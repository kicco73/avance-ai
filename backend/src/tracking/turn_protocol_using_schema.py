from __future__ import annotations

from typing import AsyncIterator

from ai.llm_provider import MetadataCallback
from logging_factory import LoggerFactory
from tracking.tag_prompt_builder import TagPromptBuilder
from tracking.turn_protocol import TurnProtocol

logger = LoggerFactory.get_logger(__name__)

EMBED_AUDIO_TAG_PROMPT = """
Definition of audio metadata:
	- a string designed for text-to-speech, not for reading.
	- Assume the user cannot see the screen at all.
	- Never refer to anything written on screen.
	- Use a nice, warm, human, non-robotic, constructive tone.
	- Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always fill in the 'audio' field of your structured response with the audio metadata value described above.
"""

EMBED_SIGNAL_TAG_PROMPT = """
Definition of signals metadata:
	- a string containing a JSON object, formatted as valid JSON text (e.g. "{\"mood\": 50.2}"),
	 not a nested object.
	- it is vitally important to always calculate and return the value for each and any signal specified in the list below.
	- put all of the signals using their own name as the key and their value as the value.

Always fill in the 'signals' field of your structured response:
"""

EMBED_ENV_TAG_PROMPT = """
Definition of env metadata:
	- a persistent, cross-session memory of free-form facts about the
	  user/conversation (e.g. preferences, ongoing goals) — distinct from
	  signals, which are re-evaluated fresh every turn.

Always fill in the 'env' field of your structured response:
	- format is a string containing plain a "name: value" pair, one per line.
	- Only include a variable name when you are actually reporting something new or
	  changed — omit the ones that haven't changed.

Current env memory:
"""
# Batch-only variants (BatchSignalSource, covering several turns in one
# call) — a single live turn or turn-by-turn replay uses the plain
# EMBED_SIGNAL_TAG_PROMPT/EMBED_ENV_TAG_PROMPT above instead, since it has
# no turn-numbering concept at all to get wrong. Keeping the two totally
# separate (rather than one prompt trying to describe both shapes) is
# deliberate — the shared version proved unstable across single-turn
# calls (extra rows, wrong turn numbers, missing turn-number prefix).
EMBED_SIGNAL_BATCH_TAG_PROMPT = """
Definition of signals metadata:
	- a small CSV table, as plain text (not a JSON object).
	- first row: the signal names, comma-separated, e.g. "mood,engagement".
	- one data row per turn, each starting with that turn's own number — the same
	  number shown on its "[Turn N]" marker in the conversation transcript —
	  followed by that turn's values. The transcript's turn numbers always run
	  1, 2, 3, ... with no gaps, so with 3 marked turns you write exactly 3 rows.
	- it is vitally important to always calculate and return a value for each and any
	  signal specified in the list below, for every turn marked in the transcript —
	  never skip one, never merge two into one row.
	- after the last turn's row, write one final row whose only cell is the
	  text [eof], exactly:
	  mood,engagement
	  1,50.2,70
	  2,52.0,68
	  3,60.0,75
	  [eof]
	- never write that [eof] row before every turn has its own row above it.

Always fill in the 'signals' field of your structured response:
"""

EMBED_ENV_BATCH_TAG_PROMPT = """
Definition of env metadata:
	- a persistent, cross-session memory of free-form facts about the
	  user/conversation (e.g. preferences, ongoing goals) — distinct from
	  signals, which are re-evaluated fresh every turn.

Always fill in the 'env' field of your structured response:
	- plain text, not JSON. One line per turn holding just that turn's own
	  number followed by a colon — the same number shown on its "[Turn N]"
	  marker in the conversation transcript — then, on the following lines,
	  one "key=value" pair per line for each variable you are actually
	  reporting as new or changed that turn (zero of them when nothing
	  changed). The transcript's turn numbers always run 1, 2, 3, ... with
	  no gaps, so with 3 marked turns you write exactly 3 turn headers:
	  1:
	  favorite_color=blue
	  2:
	  3:
	  mood=better
	  [eof]
	- one header per turn marked in the transcript — never skip one, never
	  merge two into one header.
	- after the last turn's header (and its key=value lines, if any), write
	  one final line containing only the text [eof], exactly as shown above —
	  never write it before every turn has its own header above it.
"""

EMBED_REACTION_TAG_PROMPT = """
Definition of reaction metadata:
	- the key of one reaction from the project's own declared reaction
	  vocabulary, chosen to react to the user's last message.
	- leave it empty when no declared reaction fits this turn.

Always fill in the 'reaction' field of your structured response with the
reaction key described above, or leave it empty.
"""

SCHEMA_ORDER_PROMPT = """"
Respond with the structured JSON object described by the response 
schema, filling in its fields in this order:
"""

class TurnProtocolUsingSchema(TurnProtocol):

	prompt_preambles = {
		'env': EMBED_ENV_TAG_PROMPT,
		'audio': EMBED_AUDIO_TAG_PROMPT,
		'signals': EMBED_SIGNAL_TAG_PROMPT,
		'reaction': EMBED_REACTION_TAG_PROMPT,
		'text': '',
		'signals_batch': EMBED_SIGNAL_BATCH_TAG_PROMPT,
		'env_batch': EMBED_ENV_BATCH_TAG_PROMPT,
	}

	schema = {
		"audio": "Short textual version for text-to-speech.",
		"env": "Updated memory state. Include all current context keys in the form key: value, one per line, rendered as text.",
		"signals": "JSON dictionary containing required calculated signal values, rendered as text.",
		"reaction": "The key of a declared reaction to react to the user's last message with, or empty if none fits, rendered as text.",
		"text": "Normal textual response to the user, in markdown format, rendered as text.",
		"signals_batch": "CSV table of calculated signal values: header row of signal names, then one row per turn marked in the transcript, each starting with that turn's own [Turn N] number (always 1, 2, 3, ... with no gaps), then a final row whose only cell is the text [eof], e.g. \"mood,engagement\\n1,50.2,70\\n2,52.0,68\\n[eof]\", rendered as text.",
		"env_batch": "Plain text (not JSON): one '<N>:' header line per turn marked in the transcript (that turn's own [Turn N] number, always 1, 2, 3, ... with no gaps), followed by that turn's own 'key=value' lines (none when nothing changed), then a final line containing only the text [eof], e.g. \"1:\\nfavorite_color=blue\\n2:\\n[eof]\", rendered as text.",
	}

	def _generate_reply(self, prompt: str, chat_history: list[dict], on_metadata: MetadataCallback,) -> AsyncIterator[str]:

		schema = {tag: self.schema[tag] for tag in self.include_tags}
		order_list = [f'\t- {tag}' for tag in schema.keys()]
		order = '\n'.join(order_list)
		prompt = f"{prompt}\n\n{SCHEMA_ORDER_PROMPT}\n{order}"

		return self._ai_service.generate_stream_with_metadata(
			prompt, chat_history, on_metadata=on_metadata, schema=schema
		)

	def generate_reply_with_schema(
		self, base_prompt: str, tag_specs: list[tuple[str, str]], chat_history: list[dict], on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		preambles = TagPromptBuilder().build(tag_specs, self.prompt_preambles)
		schema = TagPromptBuilder().build(tag_specs, self.schema)

		content = [preambles[tag] for tag, _ in tag_specs] + [base_prompt]
		prompt = "\n\n".join(content)

		order_list = [f'\t- {tag}' for tag in schema.keys()]
		order = '\n'.join(order_list)
		prompt = f"{prompt}\n\n{SCHEMA_ORDER_PROMPT}\n{order}"

		return self._ai_service.generate_stream_with_metadata(
			prompt, chat_history, on_metadata=on_metadata, schema=schema
		)

