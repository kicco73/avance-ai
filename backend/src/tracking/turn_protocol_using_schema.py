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
	- one data row per turn this response covers, each starting with the turn
	  number followed by that turn's values, e.g. "1,50.2,70" — numbered 1, 2, 3,
	  ... always restarting at 1 for THIS response, never the turn's absolute
	  position in the conversation history.
	- it is vitally important to always calculate and return a value for each and any signal
	  specified in the list below, for every turn you are asked to cover.

Always fill in the 'signals' field of your structured response:
"""

EMBED_ENV_BATCH_TAG_PROMPT = """
Definition of env metadata:
	- a persistent, cross-session memory of free-form facts about the
	  user/conversation (e.g. preferences, ongoing goals) — distinct from
	  signals, which are re-evaluated fresh every turn.

Always fill in the 'env' field of your structured response:
	- a JSON object, as plain text, e.g. {"1": {"favorite_color": "blue"}}.
	- one entry per turn this response covers, keyed "1", "2", "3", ... — always
	  restarting at "1" for THIS response, never the turn's absolute position in
	  the conversation history.
	- each entry's value is an object of only the variable names you are actually
	  reporting as new or changed that turn — map it to {} when nothing changed.
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
		"signals_batch": "CSV table of calculated signal values: header row of signal names, then one row per turn starting with its turn number, e.g. \"mood,engagement\\n1,50.2,70\", rendered as text.",
		"env_batch": "JSON object of updated memory state: one entry per turn keyed by its 1-based turn number, each entry mapping only the changed keys (an empty object if none), rendered as text.",
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

