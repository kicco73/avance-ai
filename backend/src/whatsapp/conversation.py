from __future__ import annotations

import asyncio

from db import Db
from system import bus
from system.bus import (
    INPUT_BUTTON, INPUT_TEXT, SESSION_CREATE, SESSION_ENTER, Message,
)
from system.logging_factory import LoggerFactory
from system.web_session import WebSession
from turn.turn_service import TurnService
from whatsapp import notices
from whatsapp.inbound_voice_note import InboundVoiceNote
from whatsapp.markdown import to_whatsapp_markdown
from whatsapp.outbound import Outbound
from whatsapp.replies import Reply, TextReply, VoicePolicy, VoiceReply
from whatsapp.webhook import IncomingMessage

logger = LoggerFactory.get_logger(__name__)

CHANNEL = __package__

ACCEPT_TERMS = "__whatsapp_accept_terms__"
_ACCEPT_TERMS_BUTTON = {"name": ACCEPT_TERMS, "ui_button": notices.ACCEPT_TERMS_LABEL, "ui_description": None}


class _Entering(object):

    async def offered(self, conversation, actions: list[dict]) -> None:
        await conversation.release()


class _Answering(object):

    async def offered(self, conversation, actions: list[dict]) -> None:
        await conversation.hold(actions)


_ENTERING = _Entering()
_ANSWERING = _Answering()


class Conversation(object):

    def __init__(
        self, sender: str, user: dict, outbound: Outbound, client, voice: VoicePolicy,
        turn_service: TurnService, db: Db,
    ) -> None:
        self.id = sender
        self._user = user
        self._outbound = outbound
        self._client = client
        self._voice = voice
        self._turn_service = turn_service
        self._db = db
        self._session_id: int | None = None
        self._session_channel: str | None = None
        self._stage: _Entering | _Answering = _ENTERING
        self._choices: list[dict] = []
        self._held: Reply | None = None
        self._reply: TextReply | VoiceReply = TextReply()
        self._lock = asyncio.Lock()

    async def receive(self, incoming: IncomingMessage) -> None:
        async with self._lock:
            await incoming.routed(self)

    async def said(self, text: str) -> None:
        self._reply = self._voice.for_typed()
        for session_id in filter(None, [await self._resolved()]):
            await self._publish(INPUT_TEXT, {"text": text}, session_id)

    async def tapped(self, action_id: str) -> None:
        self._reply = self._voice.for_typed()
        for _ in filter(ACCEPT_TERMS.__eq__, [action_id]):
            await self._accepted_terms()
            return
        for session_id in filter(None, [await self._resolved()]):
            await self._publish(INPUT_BUTTON, {"id": action_id}, session_id)

    async def spoke(self, incoming: IncomingMessage) -> None:
        self._reply = self._voice.for_spoken()
        for session_id in filter(None, [await self._resolved()]):
            note = InboundVoiceNote(self._client, incoming, self._envelope(session_id))
            for _ in filter(lambda heard: not heard, [await note.heard()]):
                await self.notify(note.notice())

    async def _resolved(self) -> int | None:
        self._stage = _ENTERING
        self._session_id = None
        self._session_channel = None
        await self._publish(SESSION_ENTER, {"session_type": "live"})
        for _ in filter(None, [self._elsewhere()]):
            logger.info(f"WhatsApp: {self.id} continues here — the open session is on {self._session_channel}.")
            self._session_id = None
            await self._publish(SESSION_CREATE, {"session_type": "live"})
        for session_id in filter(None, [self._session_id]):
            self._stage = _ANSWERING
            return session_id
        return None

    def _elsewhere(self) -> bool:
        return self._session_id is not None and self._session_channel not in (None, CHANNEL)

    def watching(self, session_id: int | None) -> bool:
        return session_id is not None and session_id == self._session_id

    async def _publish(self, type: str, body: dict, session_id: int | None = None) -> None:
        await bus.publish(self._envelope(session_id, type=type, body=body))

    def _envelope(self, session_id: int | None, type: str = INPUT_TEXT, body: dict | None = None) -> Message:
        return Message(
            type=type, body=body or {}, username=self._user["id"],
            project_id=self._project_id(),
            session_id=session_id, channel=CHANNEL, origin_id=self.id,
        )

    async def informed(self, body: dict, session_id: int | None) -> None:
        self._session_id = session_id
        self._session_channel = body.get("channel")

    async def refused(self, body: dict) -> None:
        await self.release()
        await notices.for_reason(body.get("reason")).delivered(self)

    async def ended(self) -> None:
        self._session_id = None
        self._session_channel = None

    async def failed(self, body: dict) -> None:
        await self.release()
        await notices.for_code(body.get("code")).delivered(self)

    async def announced(self, body: dict) -> None:
        self._reply.announced(str(body.get("text") or ""))

    async def offered(self, body: dict) -> None:
        self._choices = body.get("actions") or []
        await self._stage.offered(self, self._choices)

    async def hold(self, actions: list[dict]) -> None:
        held, self._held = self._held, None
        for reply in filter(None, [held]):
            await self._outbound.answer(self.id, reply, actions, self._reply)
            return
        for choices in filter(None, [actions]):
            await self._outbound.offer(self.id, notices.OPTIONS_PROMPT, choices)

    async def release(self) -> None:
        held, self._held = self._held, None
        for reply in filter(None, [held]):
            await self._outbound.say(self.id, reply, self._reply)

    async def said_back(self, body: dict) -> None:
        await self.release()
        self._held = Reply(to_whatsapp_markdown(str(body.get("text") or "")), self._audio_text(body))

    def spoken_reply(self, spoken) -> None:
        self._reply.spoken_reply(spoken)

    async def notify(self, text: str) -> None:
        await self._outbound.say(self.id, Reply(text), TextReply())

    async def notify_keeping_choices(self, text: str) -> None:
        await self._outbound.answer(self.id, Reply(text), self._choices, TextReply())

    async def ask_to_accept_terms(self) -> None:
        with self._as_the_sender():
            status = self._turn_service.get_legal_terms_status(self._project_id())
        await self._outbound.answer(
            self.id, Reply(to_whatsapp_markdown(status["content"] or "")),
            [_ACCEPT_TERMS_BUTTON], TextReply(),
        )

    async def _accepted_terms(self) -> None:
        with self._as_the_sender():
            self._turn_service.accept_legal_terms(self._project_id())
        await self.notify(notices.TERMS_ACCEPTED)

    def _audio_text(self, body: dict) -> str | None:
        for message_id in filter(None, [body.get("assistant_message_id")]):
            return (self._db.get_message(message_id) or {}).get("audio_text") or None
        return None

    def _project_id(self) -> str | None:
        return self._db.get_active_project_id(self._user["id"])

    def _as_the_sender(self):
        return WebSession().for_sender(self._user["id"], role=self._user["role"], channel=CHANNEL)
