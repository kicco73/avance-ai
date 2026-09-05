from __future__ import annotations

from .base import MetadataChannel

EMBED_AUDIO_TAG_PROMPT = """
Definition of audio metadata:
	- a string designed for text-to-speech, not for reading.
	- Assume the user cannot see the screen at all.
	- Never refer to anything written on screen.
	- Use a nice, warm, human, non-robotic, constructive tone.
	- Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always fill in the 'audio' field of your structured response with the audio metadata value described above.
"""


class AudioChannel(MetadataChannel):
	tag = "audio"
	preamble = EMBED_AUDIO_TAG_PROMPT
	schema_description = "Short textual version for text-to-speech."
