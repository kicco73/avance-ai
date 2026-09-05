from __future__ import annotations

import json

from logging_factory import LoggerFactory

from .base import MetadataChannel

logger = LoggerFactory.get_logger(__name__)

EMBED_TRANSLATE_TAG_PROMPT = """
Definition of translations metadata:
	- a string containing a JSON object, formatted as valid JSON text (e.g. "{\"advance\": \"Avanti\"}"),
	 not a nested object.
	- one entry per label listed below: its own name as the key, and a translation of its text into
	  the same language the user's last message is written in, as the value.
	- translate the text naturally for its own UI context; never translate the name itself (the key).
	- if a label is already in the right language, or you cannot confidently translate it, return it
	  unchanged rather than guessing.

Always fill in the 'translations' field of your structured response with a JSON object mapping
each name below to its translated label:
"""


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
	tag = "translations"
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
			logger.error(f"translations: {exc} -- raw: {raw!r}")
		return {**self._originals, **translated}
