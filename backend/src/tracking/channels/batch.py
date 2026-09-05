from __future__ import annotations

from typing import Any, NoReturn

from logging_factory import LoggerFactory
from try_again_error import TryAgainError

from .base import MetadataChannel

logger = LoggerFactory.get_logger(__name__)

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
