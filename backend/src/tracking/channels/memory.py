from __future__ import annotations

from tracking.env import Env

from .base import MetadataChannel

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
