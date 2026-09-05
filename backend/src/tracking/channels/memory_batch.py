from __future__ import annotations

from .batch import BATCH_END_MARKER, BatchChannel

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
