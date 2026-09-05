from __future__ import annotations

from .base import MetadataChannel


class TextChannel(MetadataChannel):
	tag = "text"
	preamble = ""
	schema_description = "Normal textual response to the user, in markdown format, rendered as text."

	def __init__(self, base_prompt: str) -> None:
		super().__init__(base_prompt)
