from __future__ import annotations

import httpx

from system import bus
from system.bus import INPUT_AUDIO, INPUT_TEXT, Message
from system.logging_factory import LoggerFactory
from whatsapp import notices
from whatsapp.webhook import IncomingMessage

logger = LoggerFactory.get_logger(__name__)


class InboundVoiceNote(object):

    def __init__(self, client, message: IncomingMessage, envelope: Message) -> None:
        self._client = client
        self._message = message
        self._envelope = envelope
        self._decoded: list[str] = []
        self._notice = notices.AUDIO_NOT_UNDERSTOOD

    async def heard(self) -> bool:
        bus.subscribe(INPUT_TEXT, self._take)
        try:
            await bus.publish_with_bounceback(self._audio(), self)
        except httpx.HTTPError as exc:
            logger.warning(f"WhatsApp [{self._message.id}]: media download failed: {exc}")
            return False
        finally:
            bus.unsubscribe(INPUT_TEXT, self._take)
        for text in self._decoded[:1]:
            logger.info(f"WhatsApp [{self._message.id}]: decoded to {text[:80]!r}")
            return True
        logger.info(f"WhatsApp [{self._message.id}]: no decoder produced text for this voice note.")
        return False

    def notice(self) -> str:
        return self._notice

    async def bounced(self, message: Message) -> None:
        logger.info(f"WhatsApp [{self._message.id}]: nothing in this build decodes a voice note.")
        self._notice = notices.UNSUPPORTED_AUDIO

    def _audio(self) -> Message:
        return Message(
            type=INPUT_AUDIO, body={"audio": self._fetch}, mime="audio/ogg",
            username=self._envelope.username, project_id=self._envelope.project_id,
            session_id=self._envelope.session_id, channel=self._envelope.channel,
            origin_id=self._envelope.origin_id,
        )

    async def _fetch(self) -> bytes:
        audio, mime_type = await self._client.download_media(str(self._message.audio_id))
        logger.info(f"WhatsApp [{self._message.id}]: downloaded {len(audio)} bytes of {mime_type}.")
        return audio

    async def _take(self, converted: Message) -> None:
        for _ in filter(None, [converted.origin_id == self._envelope.origin_id]):
            self._decoded.append(str((converted.body or {}).get("text") or ""))
