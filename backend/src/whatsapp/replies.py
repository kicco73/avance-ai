from __future__ import annotations

from dataclasses import dataclass

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


@dataclass(frozen=True)
class Reply:
    text: str
    audio_text: str | None = None


class TextReply(object):

    async def send(self, client, to: str, reply: Reply) -> None:
        await client.send_text(to, reply.text)

    async def spoke(self, client, to: str, reply: Reply) -> bool:
        return False

    def spoken_reply(self, spoken) -> None:
        pass

    def announced(self, text: str) -> None:
        pass


class VoiceReply(object):

    def __init__(self, voice_notes) -> None:
        self._voice_notes = voice_notes

    async def send(self, client, to: str, reply: Reply) -> None:
        for _ in filter(lambda spoken: not spoken, [await self.spoke(client, to, reply)]):
            await client.send_text(to, reply.text)

    async def spoke(self, client, to: str, reply: Reply) -> bool:
        from whatsapp.audio import WHATSAPP_AUDIO_MIME

        for audio_text in filter(None, [reply.audio_text]):
            try:
                mp3 = await self._voice_notes.mp3_for(audio_text)
                if not mp3:
                    logger.warning("WhatsApp: nothing was spoken for this reply, falling back to text.")
                    return False
                media_id = await client.upload_media(mp3, WHATSAPP_AUDIO_MIME)
                await client.send_audio(to, media_id)
                logger.info(f"WhatsApp: audio message sent to {to} ({len(mp3)} bytes, media {media_id}).")
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"WhatsApp: voice note not sent ({exc}), falling back to text.")
                return False
        return False

    def spoken_reply(self, spoken) -> None:
        spoken.want()

    def announced(self, text: str) -> None:
        self._voice_notes.on_metadata("audio", text)


class VoicePolicy(object):

    def __init__(self, policy: str, voice_notes) -> None:
        self._policy = policy
        self._modes = {True: VoiceReply(voice_notes), False: TextReply()}

    def for_typed(self) -> TextReply | VoiceReply:
        return self._modes[self._policy == "always"]

    def for_spoken(self) -> TextReply | VoiceReply:
        return self._modes[self._policy in ("always", "when-spoken-to")]
