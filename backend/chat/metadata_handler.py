from __future__ import annotations

EMBED_METADATA_PROMPT = """
Definition of audio metadata:
    - a string designed for text-to-speech, not for reading.
    - Assume the user cannot see the screen at all.
    - Never refer to anything written on screen.
    - Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always add a [avance]...[/avance] tag at the end of every response.
    - Write the content inside it as a dictionary in JSON format.
        - put the audio metadata using "audio" as the key and its value as the audio metadata value.
        - put a key "signals" as a dictionary
            - put all of the using their name as the key and their value as the value.
"""


class MetadataHandler(object):
    @staticmethod
    def build_prompt(signal_definitions: str) -> str:
        return signal_definitions + "\n" + EMBED_METADATA_PROMPT

    @staticmethod
    def audio_text(metadata: dict | None) -> str | None:
        return (metadata or {}).get("audio")

    @staticmethod
    def signal_values(metadata: dict | None) -> dict | None:
        return (metadata or {}).get("signals")
