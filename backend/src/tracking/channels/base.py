from __future__ import annotations

from abc import ABC
from typing import Any


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
