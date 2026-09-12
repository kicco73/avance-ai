"""Listen, as a Bus participant: the thing that turns speech into text.

This is the whole of Listen's contribution to a conversation, and it is
deliberately shaped so that nobody has to know it exists. It registers
for `input.audio` and answers with `input.text`; a channel that receives
a voice note asks the Bus whether anything decodes audio and publishes
if so, without naming speech-to-text, this module, or ListenService.

The converted message keeps its envelope, so what comes out still says
which conversation it belongs to and — through `converted_from` — that
it arrived as speech rather than typing.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from system import bus
from system.bus import INPUT_AUDIO, INPUT_TEXT, Message
from listen.listen_service import ListenService, ListenServiceError
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

#: How the audio itself reaches the decoder. A channel that already holds
#: the bytes passes them; one that holds a reference passes a callable
#: that fetches them, so nothing is downloaded for a message no one will
#: decode.
AudioSource = bytes | Callable[[], Awaitable[bytes]]


class SpeechDecoder:
    """Registered on the Bus for `input.audio`."""

    def __init__(self, listen_service: ListenService) -> None:
        self._listen_service = listen_service

    def register(self) -> None:
        bus.subscribe(INPUT_AUDIO, self.decode)

    async def decode(self, message: Message) -> None:
        audio = await self._bytes_of((message.body or {}).get("audio"))
        if not audio:
            logger.warning("Nothing to transcribe for %s.", message.origin_id)
            return
        try:
            text = (await self._listen_service.transcribe(audio)).strip()
        except ListenServiceError as exc:
            logger.warning("Transcription failed for %s: %s", message.origin_id, exc)
            return
        if not text:
            logger.info("Transcription of %s came back empty.", message.origin_id)
            return
        await bus.publish(message.converted(INPUT_TEXT, {"text": text}))

    @staticmethod
    async def _bytes_of(source: AudioSource) -> bytes:
        return await source() if callable(source) else source
