from __future__ import annotations

import csv
import io

from .batch import BATCH_END_MARKER, BatchChannel

# A single live turn or turn-by-turn replay uses SignalsChannel's own plain
# EMBED_SIGNAL_TAG_PROMPT instead, since it has no turn-numbering concept at
# all to get wrong. Keeping the two totally separate (rather than one prompt
# trying to describe both shapes) is deliberate — the shared version proved
# unstable across single-turn calls (extra rows, wrong turn numbers, missing
# turn-number prefix).
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
