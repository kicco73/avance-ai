from __future__ import annotations

EMBED_METADATA_PROMPT = """
Definition of audio metadata:
    - a string designed for text-to-speech, not for reading.
    - Assume the user cannot see the screen at all.
    - Never refer to anything written on screen.
    - Use a nice, warm, human, non-robotic, constructive tone.
    - Keep the audio metadata always concise (ideally under 5 seconds), but never omit information required to solve the task.

Always add a [audio]...[/audio] tag at the very beginning of every response:
    - put the audio metadata value between the markups.

Always add a [avance]...[/avance] tag at the end of every response.
    - Write the content inside it as a dictionary in JSON format.
        - put a key "signals" as a dictionary
            - put all of the using their name as the key and their value as the value.
"""


class MetadataHandler(object):
    @staticmethod
    def build_prompt(signal_definitions: str) -> str:
        return signal_definitions + "\n" + EMBED_METADATA_PROMPT

    @staticmethod
    def signal_values(metadata: dict | None) -> dict | None:
        return (metadata or {}).get("signals")
