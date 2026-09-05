"""Metadata channels — the schema/prompt-building unit for one field of a
turn's structured JSON response (see turn_protocol_using_schema.py). Each
channel owns everything specific to its own field: the static preamble and
this turn's own dynamic content that make up its slice of the system
prompt, its one-line JSON-schema description, and how to decode the raw
string the model returns for it. Adding a new field to what a turn can ask
the model for means adding one channel class here and appending it to
whatever list a caller builds — nothing else needs to change shape.
"""
from __future__ import annotations

import csv
import io
import json
from abc import ABC
from typing import Any, NoReturn

from logging_factory import LoggerFactory
from tracking.env import Env
from try_again_error import TryAgainError

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

EMBED_MEMORY_TAG_PROMPT = """
Definition of memory metadata:
	- your own persistent, cross-session notes: free-form facts about the
	  user/conversation (e.g. preferences, ongoing goals) — distinct from
	  signals, which are re-evaluated fresh every turn.
	- only you read and write these notes; nothing else in the system does.
	- the automaton's own variables (the "Current environment" block, when
	  present) are NOT memory: never write them here — to change one, call
	  the env source's `update` tool instead.

Always fill in the 'memory' field of your structured response:
	- format is a string containing plain "name: value" pairs, one per line.
	- Only include a note's name when you are actually reporting something new or
	  changed — omit the ones that haven't changed.

Current memory:
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

EMBED_MEMORY_BATCH_TAG_PROMPT = """
Definition of memory metadata:
	- your own persistent, cross-session notes: free-form facts about the
	  user/conversation (e.g. preferences, ongoing goals) — distinct from
	  signals, which are re-evaluated fresh every turn.
	- only you read and write these notes; nothing else in the system does.

Always fill in the 'memory' field of your structured response:
	- plain text, not JSON. One line per turn holding just that turn's own
	  number followed by a colon — the same number shown on its "[Turn N]"
	  marker in the conversation transcript — then, on the following lines,
	  one "key=value" pair per line for each note you are actually
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

EMBED_TRANSLATE_TAG_PROMPT = """
Definition of button_translations metadata:
	- a string containing a JSON object, formatted as valid JSON text (e.g. "{\"advance\": \"Avanti\"}"),
	 not a nested object.
	- one entry per label listed below: its own name as the key, and a translation of its text into
	  the same language the user's last message is written in, as the value.
	- translate the text naturally for a UI label; never translate the name itself (the key).
	- if a label is already in the right language, or you cannot confidently translate it, return it
	  unchanged rather than guessing.

Always fill in the 'button_translations' field of your structured response with a JSON object mapping
each name below to its translated label:
"""


class MetadataChannel(ABC):
	"""One field of the structured JSON response a turn asks the model
	for. `tag` is the wire name — both the on_metadata key and the JSON
	schema field name. `preamble` is this channel's own static
	instructions; `content` (set at construction) is this turn's own
	dynamic text, appended right after the preamble in the prompt, ""
	when there is none. `decode` turns the model's raw string answer for
	this tag into whatever a caller actually wants — default: passed
	through unchanged."""
	tag: str
	preamble: str
	schema_description: str

	def __init__(self, content: str = "") -> None:
		self.content = content

	def decode(self, raw: str) -> Any:
		return raw


class TextChannel(MetadataChannel):
	tag = "text"
	preamble = ""
	schema_description = "Normal textual response to the user, in markdown format, rendered as text."

	def __init__(self, base_prompt: str) -> None:
		super().__init__(base_prompt)


class AudioChannel(MetadataChannel):
	tag = "audio"
	preamble = EMBED_AUDIO_TAG_PROMPT
	schema_description = "Short textual version for text-to-speech."


class MemoryChannel(MetadataChannel):
	tag = "memory"
	preamble = EMBED_MEMORY_TAG_PROMPT
	schema_description = (
		"Memory delta: only your own notes that are new or whose value changed this turn, in the form "
		"key: value, one per line, rendered as text. Empty when nothing changed. Never the automaton's "
		"environment variables."
	)

	def __init__(self, env: Env) -> None:
		super().__init__(env.memory_as_text())

	def decode(self, raw: str) -> dict[str, str]:
		"""Single-turn format only: one "key: value" pair per line,
		optionally prefixed with "-"; blank lines and anything without a
		':' are ignored rather than raising — deliberately forgiving,
		since this is model output."""
		memory: dict[str, str] = {}
		for line in (raw or "").splitlines():
			line = line.strip()
			if line.startswith("-"):
				line = line[1:].strip()
			if not line or ":" not in line:
				continue
			key, _, value = line.partition(":")
			key = key.strip()
			if key:
				memory[key] = value.strip()
		return memory


class SignalsChannel(MetadataChannel):
	tag = "signals"
	preamble = EMBED_SIGNAL_TAG_PROMPT
	schema_description = "JSON dictionary containing required calculated signal values, rendered as text."

	def __init__(self, signal_definition: str | None) -> None:
		super().__init__(signal_definition or "")

	def decode(self, raw: str) -> dict[str, float]:
		"""Single-turn format only: a JSON object, e.g. '{"mood": 50.2}'."""
		signals: dict[str, Any] = {}
		if not raw:
			return signals
		try:
			signals = json.loads(raw) or {}
			assert isinstance(signals, dict)
		except Exception as exc:
			logger.error(f"{exc} -- raw signal: {raw}")
		return signals


class ReactionChannel(MetadataChannel):
	tag = "reaction"
	preamble = EMBED_REACTION_TAG_PROMPT
	schema_description = (
		"The key of a declared reaction to react to the user's last message with, or empty if none "
		"fits, rendered as text."
	)

	def __init__(self, reaction_definition: str | None) -> None:
		super().__init__(reaction_definition or "")

	def decode(self, raw: str) -> str | None:
		return raw.strip() or None


# The batch formats' own end-of-output marker (see BatchChannel) —
# bracketed and lowercase specifically so it can never collide with a real
# turn header ("<N>:", always just digits) or a real "key=value" line
# (always contains "="): "[eof]" is neither.
BATCH_END_MARKER = "[eof]"


class MetadataTurnMismatch(TryAgainError):
	"""Raised the instant a batch signals/memory response fails to
	demonstrably cover every turn the batch call was asked for — a
	single bad line/row, a missing or extra turn, or a missing
	BATCH_END_MARKER all raise this immediately, on the spot, rather
	than being logged and skipped so parsing can keep going. No partial
	recovery, ever: a response that's wrong or incomplete anywhere is
	wrong as a whole, and whatever was already parsed before the bad
	part is discarded along with it. Always a model mistake, always
	worth another attempt (TryAgainError): none of these failure modes
	are guaranteed to repeat on a fresh sample."""
	def __init__(self, message: str) -> None:
		super().__init__(message)


class BatchChannel(MetadataChannel):
	"""Shared plumbing for the two batch-turn channels (SignalsBatchChannel,
	MemoryBatchChannel): both decode a response covering several turns in one
	call, and both must demonstrably cover every turn 1..expected_turns,
	terminated by BATCH_END_MARKER, before the result can be trusted — a
	response cut off right before the marker could otherwise look complete
	by coincidence."""

	def __init__(self, content: str, expected_turns: int) -> None:
		super().__init__(content)
		self.expected_turns = expected_turns

	def _fail(self, message: str, raw: str | None) -> NoReturn:
		full_message = f"{self.tag}: {message}"
		logger.error(f"{full_message} -- raw: {raw!r}")
		raise MetadataTurnMismatch(full_message)

	def _turns_in_order(self, by_turn: dict[int, Any], terminated: bool, raw: str | None) -> list[Any]:
		actual = set(by_turn.keys())
		expected = set(range(1, self.expected_turns + 1))
		if actual == expected and terminated:
			return [by_turn[i] for i in range(1, self.expected_turns + 1)]
		if actual == expected:
			self._fail(f"got all {self.expected_turns} turns but no {BATCH_END_MARKER} marker — cannot trust it's complete", raw)
		self._fail(f"expected turns {sorted(expected)}, got {sorted(actual)}", raw)


class SignalsBatchChannel(BatchChannel):
	tag = "signals"
	preamble = EMBED_SIGNAL_BATCH_TAG_PROMPT
	schema_description = (
		"CSV table of calculated signal values: header row of signal names, then one row per turn "
		"marked in the transcript, each starting with that turn's own [Turn N] number (always 1, 2, "
		"3, ... with no gaps), then a final row whose only cell is the text [eof], e.g. "
		"\"mood,engagement\\n1,50.2,70\\n2,52.0,68\\n[eof]\", rendered as text."
	)

	def __init__(self, signal_definition: str | None, expected_turns: int) -> None:
		super().__init__(signal_definition or "", expected_turns)

	def decode(self, raw: str) -> list[dict[str, float]]:
		by_turn: dict[int, dict[str, float]] = {}
		terminated = False
		rows = [row for row in csv.reader(io.StringIO(raw or "")) if any(cell.strip() for cell in row)]
		if rows:
			names = [name.strip() for name in rows[0]]
			for row in rows[1:]:
				first_cell = row[0].strip()
				if first_cell.lower() == BATCH_END_MARKER:
					terminated = True
					continue
				try:
					turn = int(first_cell)
				except ValueError:
					self._fail(f"non-numeric turn index -- row: {row}", raw)
				values: dict[str, float] = {}
				for name, raw_value in zip(names, row[1:]):
					try:
						values[name] = float(raw_value.strip())
					except ValueError:
						self._fail(f"non-numeric value for '{name}' -- row: {row}", raw)
				by_turn[turn] = values
		return self._turns_in_order(by_turn, terminated, raw)


class MemoryBatchChannel(BatchChannel):
	tag = "memory"
	preamble = EMBED_MEMORY_BATCH_TAG_PROMPT
	schema_description = (
		"Plain text (not JSON): one '<N>:' header line per turn marked in the transcript (that turn's "
		"own [Turn N] number, always 1, 2, 3, ... with no gaps), followed by that turn's own "
		"'key=value' lines (none when nothing changed), then a final line containing only the text "
		"[eof], e.g. \"1:\\nfavorite_color=blue\\n2:\\n[eof]\", rendered as text."
	)

	def __init__(self, expected_turns: int) -> None:
		# Never given real content — the batch flow embeds the starting
		# memory directly into base_prompt as literal text (see
		# BatchSignalSource.prepare_batch), unlike MemoryChannel's live
		# "Current memory:" trailer.
		super().__init__("", expected_turns)

	def decode(self, raw: str) -> list[dict[str, str]]:
		by_turn: dict[int, dict[str, str]] = {}
		terminated = False
		current_turn: int | None = None
		for line in (raw or "").splitlines():
			stripped = line.strip()
			if not stripped:
				continue
			if stripped.lower() == BATCH_END_MARKER:
				terminated = True
				current_turn = None
				continue
			header = stripped[:-1].strip() if stripped.endswith(":") else None
			if header is not None and header.isdigit():
				current_turn = int(header)
				by_turn[current_turn] = {}
				continue
			if current_turn is None:
				self._fail(f"line outside any turn header -- line: {line!r}", raw)
			if "=" not in stripped:
				self._fail(f"line without '=' -- line: {line!r}", raw)
			key, _, value = stripped.partition("=")
			key = key.strip()
			if not key:
				self._fail(f"line with an empty key -- line: {line!r}", raw)
			by_turn[current_turn][key] = value.strip()
		return self._turns_in_order(by_turn, terminated, raw)


class TranslateChannel(MetadataChannel):
	"""Translates a set of caller-named UI strings into the same language
	as the user's last message — a generic {name: original text} -> {name:
	translated text} channel, reusable for any labels a turn needs
	localized on the fly, not specific to any one kind of label. Today's
	only caller is the manual-action button labels (see
	TrackingProcessor._actions_needing_button_translation), appended as
	the turn's last channel; same "translate on the fly" convention
	TrackingProcessor.FIXED_MESSAGE_INSTRUCTIONS already uses for a
	fixed_message state. Decoding is deliberately lenient: a translated
	label is a UX nicety layered on top of an otherwise-complete reply,
	never core protocol correctness like signals/env, so a malformed
	response falls back to the original text rather than raising and
	losing the whole turn."""
	tag = "button_translations"
	preamble = EMBED_TRANSLATE_TAG_PROMPT
	schema_description = (
		"JSON object mapping each of the listed name to a translation of its label into the same "
		"language as the user's last message, rendered as text."
	)

	def __init__(self, originals: dict[str, str]) -> None:
		self._originals = originals
		content = "\n".join(f'\t- "{name}": "{text}"' for name, text in originals.items())
		super().__init__(content)

	def decode(self, raw: str) -> dict[str, str]:
		"""Always covers every name this channel was built for — one the
		model skipped, mistranslated into a non-string, or lost entirely
		to a malformed/unparseable response falls back to its own
		original text rather than leaving a gap: an untranslated label
		beats a missing one."""
		translated: dict[str, str] = {}
		try:
			value = json.loads(raw) if raw else {}
			assert isinstance(value, dict)
			translated = {k: v for k, v in value.items() if isinstance(v, str)}
		except Exception as exc:
			logger.error(f"button_translations: {exc} -- raw: {raw!r}")
		return {**self._originals, **translated}
