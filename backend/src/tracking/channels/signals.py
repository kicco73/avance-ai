from __future__ import annotations

import json
from typing import Any

from logging_factory import LoggerFactory

from .base import MetadataChannel

logger = LoggerFactory.get_logger(__name__)

EMBED_SIGNAL_TAG_PROMPT = """
Definition of signals metadata:
	- a string containing a JSON object, formatted as valid JSON text (e.g. "{\"mood\": 50.2}"),
	 not a nested object.
	- it is vitally important to always calculate and return the value for each and any signal specified in the list below.
	- put all of the signals using their own name as the key and their value as the value.

Always fill in the 'signals' field of your structured response:
"""


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
