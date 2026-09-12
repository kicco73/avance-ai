"""A voice note from Meta, as text — over the Bus, which is the only way
this channel knows of turning audio into words.

The note is published as `input.audio` and whatever `input.text` a
decoder made of it is taken back (see listen/decoder.py); the audio
travels as a callable, so nothing is downloaded from Meta for a message
no one in this build is going to read. Whether anything decodes at all is
asked first: "nobody here can listen" is an answer, and a message that
quietly disappears is not.
"""
from __future__ import annotations

import httpx

from system import bus
from system.bus import INPUT_AUDIO, INPUT_TEXT, Message
from system.logging_factory import LoggerFactory
from system.web_session import WebSession
from whatsapp.webhook import IncomingMessage

logger = LoggerFactory.get_logger(__name__)

CHANNEL = __package__

REPLY_UNSUPPORTED_AUDIO = "I can't listen to voice notes yet — please type your message."
REPLY_AUDIO_NOT_UNDERSTOOD = "I couldn't make out that voice note. Could you repeat it, or type it?"


class InboundVoiceNote(object):

    def __init__(self, client) -> None:
        self._client = client

    async def decoded_text(self, message: IncomingMessage, audio_id: str) -> str | None:
        decoded: list[str] = []

        async def take(converted: Message) -> None:
            # Only this voice note's own answer: two users' messages can
            # be decoded at the same time, and neither may take the
            # other's text.
            if converted.origin_id == message.id:
                decoded.append(str((converted.body or {}).get("text") or ""))

        async def fetch() -> bytes:
            audio, mime_type = await self._client.download_media(audio_id)
            logger.info(f"WhatsApp [{message.id}]: downloaded {len(audio)} bytes of {mime_type}.")
            return audio

        bus.subscribe(INPUT_TEXT, take)
        try:
            await bus.publish(Message(
                type=INPUT_AUDIO, body={"audio": fetch}, mime="audio/ogg",
                username=WebSession().user, channel=CHANNEL, origin_id=message.id,
            ))
        except httpx.HTTPError as exc:
            logger.warning(f"WhatsApp [{message.id}]: media download failed: {exc}")
            return None
        finally:
            bus.unsubscribe(INPUT_TEXT, take)
        if not decoded:
            logger.info(f"WhatsApp [{message.id}]: no decoder produced text for this voice note.")
            return None
        logger.info(f"WhatsApp [{message.id}]: decoded to {decoded[0][:80]!r}")
        return decoded[0]

    def notice(self) -> str:
        return REPLY_AUDIO_NOT_UNDERSTOOD if bus.handlers_for(INPUT_AUDIO) else REPLY_UNSUPPORTED_AUDIO
