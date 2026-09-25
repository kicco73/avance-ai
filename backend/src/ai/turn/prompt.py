from __future__ import annotations

import csv
import io
import re
from typing import Any, Iterable, NoReturn

from system.logging_factory import LoggerFactory
from system.try_again_error import TryAgainError

from ai import SystemPrompt
from ai.response_schema import ArrayField, BooleanField, Field, NumberField, ObjectField, StringField
from automaton.automaton import Automaton
from tracking.markdown_repairer import MarkdownRepairer

from tracking.env import Env

logger = LoggerFactory.get_logger(__name__)

SCHEMA_ORDER_PROMPT = """"
Respond with the structured JSON object described by the response
schema, filling in its fields in this order:
"""
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


def _fail(channel: str, message: str, raw: str | None) -> NoReturn:
	full_message = f"{channel}: {message}"
	logger.error(f"{full_message} -- raw: {raw!r}")
	raise MetadataTurnMismatch(full_message)


def build_output_definition_for_names(automaton: Automaton, names: Iterable[str], already_shown: Iterable[str] = ()) -> str | None:
	"""The `- Definition of output fields:` block for exactly `names`
	(declaration order, deduplicated) — None once `names` is empty."""
	unique_names = list(dict.fromkeys(names))
	if not unique_names:
		return None
	already_shown = set(already_shown)
	env_keys_by_name = {env_key.name: env_key for env_key in automaton.env_keys}

	def render(name: str) -> str:
		if name in already_shown:
			return f'\t- Output "{name}": defined above, in "Current environment".'
		return f'\t- Output "{name}":\n{env_keys_by_name[name].ai_definition}'

	return "- Definition of output fields:\n" + "\n\n".join(render(name) for name in unique_names)


_FIELD_BY_ENV_TYPE: dict[str, Field] = {
	"number": NumberField(), "string": StringField(), "bool": BooleanField(), "list": ArrayField(StringField()),
}


def build_output_fields(automaton: Automaton, names: Iterable[str]) -> dict[str, Field]:
	env_keys_by_name = {env_key.name: env_key for env_key in automaton.env_keys}
	return {name: _FIELD_BY_ENV_TYPE[env_keys_by_name[name].type] for name in dict.fromkeys(names)}


def _turns_in_order(channel: str, by_turn: dict[int, Any], expected_turns: int, terminated: bool, raw: str | None) -> list[Any]:
	"""Shared by SignalsBatchPrompt/MemoryBatchPrompt's own decode: both
	must demonstrably cover every turn 1..expected_turns, terminated by
	BATCH_END_MARKER, before the result can be trusted — a response cut
	off right before the marker could otherwise look complete by
	coincidence."""
	actual = set(by_turn.keys())
	expected = set(range(1, expected_turns + 1))
	if actual == expected and terminated:
		return [by_turn[i] for i in range(1, expected_turns + 1)]
	if actual == expected:
		_fail(channel, f"got all {expected_turns} turns but no {BATCH_END_MARKER} marker — cannot trust it's complete", raw)
	_fail(channel, f"expected turns {sorted(expected)}, got {sorted(actual)}", raw)


def _decode_turn_keyed_lines(channel: str, raw: str, expected_turns: int) -> list[dict[str, str]]:
	"""Shared by MemoryBatchPrompt/OutputBatchPrompt's own decode: both use
	the identical "<N>:" turn-header + "key=value" line format, covering
	every turn 1..expected_turns, terminated by BATCH_END_MARKER."""
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
			_fail(channel, f"line outside any turn header -- line: {line!r}", raw)
		if "=" not in stripped:
			_fail(channel, f"line without '=' -- line: {line!r}", raw)
		key, _, value = stripped.partition("=")
		key = key.strip()
		if not key:
			_fail(channel, f"line with an empty key -- line: {line!r}", raw)
		by_turn[current_turn][key] = value.strip()
	return _turns_in_order(channel, by_turn, expected_turns, terminated, raw)


class Prompt:
	"""One channel's own prompt, or a composition of several. Internally a
	dict {channel: leaf Prompt} — a freshly-constructed subclass instance
	is a dict of one entry (itself); compose() merges two such dicts with
	dict.update() semantics: same channel key, the later one wins,
	insertion order otherwise preserved (this is what fixes both the
	prompt's own text order and the JSON schema's field order). Every
	rendering method below (to_system_prompt/schema/decode_channel/
	render_text/schema_overhead_text) reads only `_channels`, so a
	composed instance never has its own leaf behaviour called — it's
	purely a container.

	`channel` is the wire name (both the on_metadata key and the JSON
	schema field name). `definition` is this channel's own static
	instructions. `content` (set at construction) is this turn's own
	dynamic text, appended right after `definition`. `field` is the type
	this channel asks the provider for; `decode` turns the value the
	provider returned, already of that type, into whatever a caller
	actually wants — default: passed through unchanged."""

	channel: str
	definition: str
	schema_description: str = ""

	def __init__(self, content: str = "") -> None:
		self.content = content
		self._channels: dict[str, "Prompt"] = {self.channel: self}

	@property
	def stable(self) -> str:
		"""This channel's own contribution to the cache-friendly, per-state
		prefix — everything that doesn't change while the turn's own
		content doesn't (see MemoryPrompt, the one override: its own
		content changes every turn, so it belongs in `volatile` instead)."""
		return "\n\n".join([self.definition, self.content])

	@property
	def volatile(self) -> str:
		"""This channel's own contribution to the per-turn tail — empty
		unless overridden (see MemoryPrompt)."""
		return ""

	def decode(self, raw: Any) -> Any:
		return raw

	def compose(self, other: "Prompt | None") -> "Prompt":
		if other is None:
			return self
		merged = Prompt.__new__(Prompt)
		merged._channels = {**self._channels, **other._channels}
		return merged

	@staticmethod
	def chain(*parts: "Prompt | None") -> "Prompt":
		"""Composes every non-None part, left to right, in the given order
		— the ordering primitive TrackingProcessor uses in place of a
		hand-built list + a static channel-order gate: which channels are
		active this turn is just which of `parts` isn't None."""
		present = [part for part in parts if part is not None]
		assert present, "Prompt.chain() needs at least one non-None part"
		result = present[0]
		for part in present[1:]:
			result = result.compose(part)
		return result

	def field(self) -> Field:
		return StringField(self.schema_description)

	def schema(self) -> dict[str, Field]:
		return {channel: leaf.field() for channel, leaf in self._channels.items()}

	def to_system_prompt(self) -> SystemPrompt:
		"""The SystemPrompt(stable, volatile) TurnProtocolUsingSchema.
		generate_reply hands to AiService — split so a provider that caches
		a prefix (see AnthropicProvider._build_system) can hit that cache
		across consecutive turns in the same automaton state."""
		order = "\n".join(f"\t- {channel}" for channel in self._channels)
		stable = "\n\n".join(leaf.stable for leaf in self._channels.values())
		stable = f"{stable}\n\n{SCHEMA_ORDER_PROMPT}\n{order}"
		volatile = "\n\n".join(leaf.volatile for leaf in self._channels.values() if leaf.volatile)
		return SystemPrompt(stable=stable, volatile=volatile)

	def render_text(self) -> str:
		"""The exact text to_system_prompt() would send, minus the
		trailing SCHEMA_ORDER_PROMPT field-order instructions and the
		stable/volatile split — split out so a caller that only wants the
		rendered text (e.g. a token estimate) doesn't have to trigger a
		real generation call to get it. Always the generic definition+
		content join, even for MemoryPrompt (whose `stable`/`volatile`
		split doesn't apply here — see its own `definition`, kept as the
		whole instructions+header text for exactly this caller)."""
		parts: list[str] = []
		for leaf in self._channels.values():
			parts += [leaf.definition, leaf.content]
		return "\n\n".join(parts)

	def schema_overhead_text(self) -> str:
		"""Every fixed bit of text to_system_prompt() adds on top of each
		channel's own dynamic `content` — every channel's own `definition`
		plus SCHEMA_ORDER_PROMPT's field-order instructions. Read-only, so
		TrackingProcessor._enforce_input_budget can size it without a real
		generation call."""
		definitions = "".join(leaf.definition for leaf in self._channels.values())
		order = "\n".join(f"\t- {channel}" for channel in self._channels)
		return f"{definitions}{SCHEMA_ORDER_PROMPT}\n{order}"

	def decode_channel(self, channel: str, raw: Any) -> Any:
		leaf = self._channels.get(channel)
		return leaf.decode(raw) if leaf is not None else raw


class TextPrompt(Prompt):
	channel = "text"
	definition = ""
	schema_description = "Normal textual response to the user, in markdown format, rendered as text."

	def __init__(self, base_prompt: str) -> None:
		super().__init__(base_prompt)
		self._markdown_repairer = MarkdownRepairer()

	def decode(self, raw: str) -> str:
		return self._markdown_repairer.repair(raw)


EMBED_AUDIO_TAG_PROMPT = """
Definition of audio metadata:
	- a string designed for text-to-speech, not for reading.
	- Assume the user cannot see the screen at all.
	- Never refer to anything written on screen.
	- Use a nice, warm, human, non-robotic, constructive tone.
	- Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always fill in the 'audio' field of your structured response with the audio metadata value described above.
"""


class AudioPrompt(Prompt):
	channel = "audio"
	definition = EMBED_AUDIO_TAG_PROMPT
	schema_description = "Short textual version for text-to-speech."


EMBED_REACTION_TAG_PROMPT = """
Definition of reaction metadata:
	- the key of one reaction from the project's own declared reaction
	  vocabulary, chosen to react to the user's last message.
	- leave it empty when no declared reaction fits this turn.

Always fill in the 'reaction' field of your structured response with the
reaction key described above, or leave it empty.
"""


class ReactionPrompt(Prompt):
	channel = "reaction"
	definition = EMBED_REACTION_TAG_PROMPT
	schema_description = (
		"The key of a declared reaction to react to the user's last message with, or empty if none "
		"fits, rendered as text."
	)

	def __init__(self, reaction_definition: str | None) -> None:
		super().__init__(reaction_definition or "")

	def decode(self, raw: str) -> str | None:
		return raw.strip() or None


EMBED_OUTPUT_TAG_PROMPT = """
Definition of output fields:
	- an object with one entry per output field declared below, each holding that field's own value.
	- it is vitally important to check each output field declared below against this turn, every turn
	  — never skip one because it seemed unlikely to apply.
	- fill in a field the moment this turn actually gives its value; set a field to null only once you
	  have checked it and this turn truly gives none.

Always fill in the 'output' field of your structured response:
"""


class OutputPrompt(Prompt):
	channel = "output"
	definition = EMBED_OUTPUT_TAG_PROMPT
	schema_description = "One entry per declared output field: its value this turn, or null when this turn gives none."

	def __init__(self, output_definition: str | None, fields: dict[str, Field]) -> None:
		super().__init__(output_definition or "")
		self._fields = fields

	def field(self) -> Field:
		return ObjectField({name: field.as_output_field() for name, field in self._fields.items()}, self.schema_description)

	def decode(self, raw: dict[str, Any] | None) -> dict[str, Any]:
		return {name: value for name, value in (raw or {}).items() if value is not None}


EMBED_SIGNAL_TAG_PROMPT = """
Definition of signals metadata:
	- an object with one numeric entry per signal specified in the list below, keyed by the signal's own name.
	- it is vitally important to always calculate and return the value for each and any signal specified in the list below.

Always fill in the 'signals' field of your structured response:
"""


class SignalsPrompt(Prompt):
	channel = "signals"
	definition = EMBED_SIGNAL_TAG_PROMPT
	schema_description = "The calculated value of each required signal."

	def __init__(self, signal_definition: str | None, names: Iterable[str]) -> None:
		super().__init__(signal_definition or "")
		self._names = sorted(set(names))

	def field(self) -> Field:
		return ObjectField({name: NumberField() for name in self._names}, self.schema_description)

	def decode(self, raw: dict[str, float] | None) -> dict[str, float]:
		return dict(raw or {})


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


class SignalsBatchPrompt(Prompt):
	channel = "signals"
	definition = EMBED_SIGNAL_BATCH_TAG_PROMPT
	schema_description = (
		"CSV table of calculated signal values: header row of signal names, then one row per turn "
		"marked in the transcript, each starting with that turn's own [Turn N] number (always 1, 2, "
		"3, ... with no gaps), then a final row whose only cell is the text [eof], e.g. "
		"\"mood,engagement\\n1,50.2,70\\n2,52.0,68\\n[eof]\", rendered as text."
	)

	def __init__(self, signal_definition: str | None, expected_turns: int, signal_names: Iterable[str]) -> None:
		super().__init__(signal_definition or "")
		self.expected_turns = expected_turns
		self.signal_names = frozenset(signal_names)

	def decode(self, raw: str) -> list[dict[str, float]]:
		by_turn: dict[int, dict[str, float]] = {}
		terminated = False
		rows = [row for row in csv.reader(io.StringIO(raw or "")) if any(cell.strip() for cell in row)]
		if rows:
			names = [name.strip() for name in rows[0]]
			if len(names) != len(set(names)) or set(names) != self.signal_names:
				_fail(self.channel, f"header {names} does not name exactly the requested signals {sorted(self.signal_names)}", raw)
			for row in rows[1:]:
				first_cell = row[0].strip()
				if first_cell.lower() == BATCH_END_MARKER:
					terminated = True
					continue
				try:
					turn = int(first_cell)
				except ValueError:
					_fail(self.channel, f"non-numeric turn index -- row: {row}", raw)
				if len(row) - 1 != len(names):
					_fail(self.channel, f"{len(row) - 1} value(s) for {len(names)} signal(s) -- row: {row}", raw)
				values: dict[str, float] = {}
				for name, raw_value in zip(names, row[1:]):
					try:
						values[name] = float(raw_value.strip())
					except ValueError:
						_fail(self.channel, f"non-numeric value for '{name}' -- row: {row}", raw)
				by_turn[turn] = values
		return _turns_in_order(self.channel, by_turn, self.expected_turns, terminated, raw)


EMBED_MEMORY_TAG_INSTRUCTIONS = """
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
"""
EMBED_MEMORY_TAG_HEADER = """
Current memory:
"""
EMBED_MEMORY_TAG_PROMPT = EMBED_MEMORY_TAG_INSTRUCTIONS + EMBED_MEMORY_TAG_HEADER


class MemoryPrompt(Prompt):
	channel = "memory"
	definition = EMBED_MEMORY_TAG_PROMPT
	schema_description = (
		"Memory delta: only your own notes that are new or whose value changed this turn, in the form "
		"key: value, one per line, rendered as text. Empty when nothing changed. Never the automaton's "
		"environment variables."
	)

	def __init__(self, env: Env) -> None:
		super().__init__(env.memory_as_text())

	@property
	def stable(self) -> str:
		return EMBED_MEMORY_TAG_INSTRUCTIONS

	@property
	def volatile(self) -> str:
		return "\n\n".join([EMBED_MEMORY_TAG_HEADER, self.content])

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


class MemoryBatchPrompt(Prompt):
	channel = "memory"
	definition = EMBED_MEMORY_BATCH_TAG_PROMPT
	schema_description = (
		"Plain text (not JSON): one '<N>:' header line per turn marked in the transcript (that turn's "
		"own [Turn N] number, always 1, 2, 3, ... with no gaps), followed by that turn's own "
		"'key=value' lines (none when nothing changed), then a final line containing only the text "
		"[eof], e.g. \"1:\\nfavorite_color=blue\\n2:\\n[eof]\", rendered as text."
	)

	def __init__(self, expected_turns: int) -> None:
		super().__init__("")
		self.expected_turns = expected_turns

	def decode(self, raw: str) -> list[dict[str, str]]:
		return _decode_turn_keyed_lines(self.channel, raw, self.expected_turns)


EMBED_OUTPUT_BATCH_TAG_PROMPT = """
Definition of output metadata:
	- each declared output field is an automaton env variable the model
	  itself directly produces — see the field definitions given
	  separately below for which fields exist and what each one means.
	- it is vitally important to check every declared output field against every turn — never skip one
	  because it seemed unlikely to apply.
	- plain text, not JSON. One line per turn holding just that turn's own
	  number followed by a colon — the same number shown on its "[Turn N]"
	  marker in the conversation transcript — then, on the following lines,
	  one "key=value" pair per line for each output field that turn
	  actually has a value for (none of them only once every field has
	  been checked and that turn truly gives none). The transcript's turn
	  numbers always run 1, 2, 3, ... with no gaps, so with 3 marked turns
	  you write exactly 3 turn headers:
	  1:
	  pnr=ABC123
	  2:
	  3:
	  rating=4.5
	  [eof]
	- one header per turn marked in the transcript — never skip one, never
	  merge two into one header.
	- after the last turn's header (and its key=value lines, if any), write
	  one final line containing only the text [eof], exactly as shown above —
	  never write it before every turn has its own header above it.

Always fill in the 'output' field of your structured response:
"""


class OutputBatchPrompt(Prompt):
	channel = "output"
	definition = EMBED_OUTPUT_BATCH_TAG_PROMPT
	schema_description = (
		"Plain text (not JSON): one '<N>:' header line per turn marked in the transcript (that turn's "
		"own [Turn N] number, always 1, 2, 3, ... with no gaps), followed by that turn's own "
		"'key=value' lines for whichever declared output fields this turn actually produces a value "
		"for (none when none apply), then a final line containing only the text [eof], e.g. "
		"\"1:\\npnr=ABC123\\n2:\\n[eof]\", rendered as text."
	)

	def __init__(self, expected_turns: int) -> None:
		super().__init__("")
		self.expected_turns = expected_turns

	def decode(self, raw: str) -> list[dict[str, str]]:
		return _decode_turn_keyed_lines(self.channel, raw, self.expected_turns)


_LANG_TAG_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")

EMBED_LANG_TAG_PROMPT = """
Definition of lang metadata:
	- "src": the language and region the labels below were originally written in.
	- "dst": the language and region the user's last message is written in — what a translation of those
	  labels should be written in.
	- always a full locale tag, lowercase language + uppercase region joined by a hyphen (e.g. "it-IT",
	  "es-ES", "en-GB", "pt-BR"), following the IETF BCP 47 standard — never a bare language code.

Always fill in the 'lang' field of your structured response with its "src" and "dst".
"""


class LangPrompt(Prompt):
	channel = "lang"
	definition = EMBED_LANG_TAG_PROMPT
	schema_description = (
		"The locale tag (IETF BCP 47, e.g. \"it-IT\") of the labels' own language (src) and of the language "
		"to translate them into (dst), the user's last message's own language."
	)

	def field(self) -> Field:
		return ObjectField({"src": StringField(), "dst": StringField()}, self.schema_description)

	def decode(self, raw: dict[str, str] | None) -> tuple[str, str]:
		src, dst = (raw or {}).get("src", ""), (raw or {}).get("dst", "")
		if _LANG_TAG_RE.match(src) and _LANG_TAG_RE.match(dst):
			return src, dst
		logger.error(f"lang: not a locale pair -- raw: {raw!r}")
		return "", ""


EMBED_TRANSLATE_TAG_PROMPT = """
Definition of translations metadata:
	- one entry per label listed below: its own name as the key, and a translation of its text into
	  the same language the user's last message is written in, as the value.
	- translate the text naturally for its own UI context; never translate the name itself (the key).
	- if a label is already in the right language, or you cannot confidently translate it, return it
	  unchanged rather than guessing.

Always fill in the 'translations' field of your structured response with each name below and its translated label:
"""


class TranslatePrompt(Prompt):
	"""Translates a set of caller-named UI strings into the same language
	as the user's last message — a generic {name: original text} -> {name:
	translated text} channel, reusable for any labels a turn needs
	localized on the fly, not specific to any one kind of label. Today's
	only caller is the manual-action button labels (see
	TrackingProcessor._button_labels_to_translate), composed as the turn's
	last channel. Decoding is deliberately lenient: a translated
	label is a UX nicety layered on top of an otherwise-complete reply,
	never core protocol correctness like signals/env, so a missing
	translation falls back to the original text rather than raising and
	losing the whole turn."""
	channel = "translations"
	definition = EMBED_TRANSLATE_TAG_PROMPT
	schema_description = "Each listed name mapped to a translation of its label into the language of the user's last message."

	def __init__(self, originals: dict[str, str]) -> None:
		self._originals = originals
		content = "\n".join(f'\t- "{name}": "{text}"' for name, text in originals.items())
		super().__init__(content)

	def field(self) -> Field:
		return ObjectField({name: StringField() for name in self._originals}, self.schema_description)

	def decode(self, raw: dict[str, str] | None) -> dict[str, str]:
		"""Always covers every name this channel was built for — one the
		model skipped or left empty falls back to its own original text
		rather than leaving a gap: an untranslated label beats a missing one."""
		translated = {name: text for name, text in (raw or {}).items() if isinstance(text, str) and text}
		return {**self._originals, **translated}
