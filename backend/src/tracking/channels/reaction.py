from __future__ import annotations

from .base import MetadataChannel

EMBED_REACTION_TAG_PROMPT = """
Definition of reaction metadata:
	- the key of one reaction from the project's own declared reaction
	  vocabulary, chosen to react to the user's last message.
	- leave it empty when no declared reaction fits this turn.

Always fill in the 'reaction' field of your structured response with the
reaction key described above, or leave it empty.
"""


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
