"""The outbound half of the channel: how a reply becomes WhatsApp
messages.

webhook.py is the inbound half — what Meta POSTs and whether to believe
it. cloud_api_client.py is the transport under both. This is the shaping
in between, and all of it is WhatsApp's own idea of what a message can
be: at most three buttons or ten list rows, titles truncated at twenty
characters, an interactive body no longer than 1024, a voice note that
has to be uploaded before it can be sent, and buttons that ride on the
last reply's text because they cannot travel alone.

None of that is a conversation, which is why it is not in the service.
What the service hands over is a list of replies and the actions now
available; every limit and every fallback below is this one API's.
"""
from __future__ import annotations

from dataclasses import dataclass

from system.logging_factory import LoggerFactory
from whatsapp.cloud_api_client import WhatsAppCloudApiClient
from whatsapp.webhook import to_whatsapp_markdown

logger = LoggerFactory.get_logger(__name__)

#: Said when there is nothing else to say but buttons still need a body.
REPLY_DONE = "Done."
REPLY_OPTIONS_PROMPT = "What would you like to do?"

# WhatsApp's own limits, every one of them.
_MAX_REPLY_BUTTONS = 3
_MAX_LIST_ROWS = 10
_BUTTON_TITLE_LIMIT = 20
_LIST_ROW_TITLE_LIMIT = 24
_LIST_ROW_DESCRIPTION_LIMIT = 72
_INTERACTIVE_BODY_LIMIT = 1024
_LIST_BUTTON_TEXT = "Options"


@dataclass(frozen=True)
class Reply:
    text: str
    # The reply's own [audio] text (Message.audio_text), when the project
    # produced one — what TalkService would speak. None = text only.
    audio_text: str | None = None


def replies_from(messages: list[dict], notice: str | None = None) -> list[Reply]:
    """Assistant messages as replies, empty ones dropped — a turn can
    persist a blank assistant row, and an empty WhatsApp message is
    refused by the Cloud API rather than ignored."""
    replies = [
        Reply(text=to_whatsapp_markdown(m["content"]), audio_text=(m.get("audio_text") or None))
        for m in messages
        if (m["content"] or "").strip()
    ]
    if notice:
        replies.append(Reply(notice))
    return replies


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class Outbound(object):
    """One object per channel, holding the transport and — when this build
    has one — whatever turns a reply's spoken text into an audio message.
    No voice note maker means text, every time, with no branch at the
    call sites."""

    def __init__(self, client: WhatsAppCloudApiClient, voice_notes=None) -> None:
        self._client = client
        self._voice_notes = voice_notes

    @property
    def can_speak(self) -> bool:
        return self._voice_notes is not None

    async def send(
        self, to: str, replies: list[Reply], manual_actions: list[dict] | None, session_id: int | None,
        voice: bool = False,
    ) -> None:
        """Each reply goes out once — as a voice note when `voice` and the
        reply has an audio text (and the note actually gets sent), as text
        otherwise. Buttons ride on the last reply's text; after a spoken
        last reply they come on a short follow-up prompt instead."""
        replies = [r for r in replies if r.text]
        if not manual_actions:
            for reply in replies:
                await self._send_one(to, reply, voice)
            return
        *leading, last = replies or [Reply(REPLY_DONE)]
        for reply in leading:
            await self._send_one(to, reply, voice)
        if voice and await self._try_voice_note(to, last):
            await self._send_with_buttons(to, REPLY_OPTIONS_PROMPT, manual_actions, session_id)
        else:
            await self._send_with_buttons(to, last.text, manual_actions, session_id)

    async def _send_one(self, to: str, reply: Reply, voice: bool) -> None:
        if voice and await self._try_voice_note(to, reply):
            return
        await self._client.send_text(to, reply.text)

    async def _try_voice_note(self, to: str, reply: Reply) -> bool:
        """True once a voice note for `reply` is on its way; False (with
        the reason logged) whenever it can't be — the caller falls back to
        text, never to silence."""
        logger.info(
            f"WhatsApp: _try_voice_note for {to}: audio_text={reply.audio_text!r} "
            f"talk_service_configured={self.can_speak}"
        )
        if not reply.audio_text or self._voice_notes is None:
            return False
        from whatsapp.audio import WHATSAPP_AUDIO_MIME

        try:
            mp3 = await self._voice_notes.mp3_for(reply.audio_text)
            if not mp3:
                logger.warning("WhatsApp: talk-service produced no audio, falling back to text.")
                return False
            media_id = await self._client.upload_media(mp3, WHATSAPP_AUDIO_MIME)
            await self._client.send_audio(to, media_id)
            logger.info(f"WhatsApp: audio message sent to {to} ({len(mp3)} bytes, media {media_id}).")
            return True
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad: PyAV's own encoding failures (a partial/
            # truncated WAV from a TalkService generation that ended early)
            # raise its own exception types, not ValueError/httpx.HTTPError —
            # letting one of those escape here would propagate past send()'s
            # caller and leave the user with no reply at all instead of the
            # text fallback this docstring promises.
            logger.warning(f"WhatsApp: voice note not sent ({exc}), falling back to text.")
            return False

    async def _send_with_buttons(
        self, to: str, body: str, manual_actions: list[dict], session_id: int | None,
    ) -> None:
        if len(body) > _INTERACTIVE_BODY_LIMIT:
            await self._client.send_text(to, body)
            body = REPLY_OPTIONS_PROMPT

        actions = manual_actions
        if len(actions) > _MAX_LIST_ROWS:
            logger.warning(f"WhatsApp: state has {len(actions)} manual actions, sending only the first {_MAX_LIST_ROWS}.")
            actions = actions[:_MAX_LIST_ROWS]

        action_names = [a["name"] for a in actions]
        if len(actions) <= _MAX_REPLY_BUTTONS:
            logger.info(f"WhatsApp: sending buttons for session {session_id}: {action_names}.")
            buttons = [(a["name"], _truncate(a["ui_button"], _BUTTON_TITLE_LIMIT)) for a in actions]
            await self._client.send_buttons(to, body, buttons)
        else:
            logger.info(f"WhatsApp: sending list for session {session_id}: {action_names}.")
            rows = [
                (
                    a["name"],
                    _truncate(a["ui_button"], _LIST_ROW_TITLE_LIMIT),
                    _truncate(a["ui_description"], _LIST_ROW_DESCRIPTION_LIMIT) if a["ui_description"] else None,
                )
                for a in actions
            ]
            await self._client.send_list(to, body, _LIST_BUTTON_TEXT, rows)
