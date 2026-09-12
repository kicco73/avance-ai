from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from system import bus
from system.bus import (
    INPUT_TEXT, OUTPUT_TEXT, OUTPUT_SPEECH, POINT_SPOKEN_REPLY, OUTPUT_ERROR, UI_BUTTONS, Message,
)
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

NO_TURN_LISTENER = "no_turn_listener"


@dataclass(frozen=True)
class TurnOutcome:
    messages: list[dict]
    manual_actions: list[dict] | None = None
    code: str | None = None


class TextReply(object):

    def spoken_reply(self, spoken) -> None:
        pass

    def announced(self, text: str) -> None:
        pass


class VoiceReply(object):

    def __init__(self, voice_notes) -> None:
        self._voice_notes = voice_notes

    def spoken_reply(self, spoken) -> None:
        spoken.want()

    def announced(self, text: str) -> None:
        self._voice_notes.on_metadata("audio", text)


@dataclass
class TurnExchange(object):
    channel: str
    username: str
    session_id: int
    text: str
    origin_id: str | None = None
    project_id: str | None = None
    voice: TextReply | VoiceReply = field(default_factory=TextReply)
    #: What this exchange produced, gathered as it is published: whole
    #: messages on `output.text`, the choices on `ui.buttons`. The
    #: terminal frame says it is over, not what was said.
    said: list[dict] = field(default_factory=list)
    actions: list[dict] | None = None
    #: Where this channel reads a persisted message from.
    db: object = None
    #: Whether the choices have been published yet — what tells the
    #: answer apart from a message the state owed before it.
    offered: bool = False

    async def run(self) -> TurnOutcome:
        self._outcome: asyncio.Future[TurnOutcome] = asyncio.get_running_loop().create_future()
        bus.subscribe(OUTPUT_ERROR, self._failed)
        bus.subscribe(OUTPUT_TEXT, self._said)
        bus.subscribe(UI_BUTTONS, self._offered)
        bus.subscribe(OUTPUT_SPEECH, self._speech)
        bus.contribute(POINT_SPOKEN_REPLY, self._spoken_reply)
        try:
            await bus.publish_with_bounceback(self._input(), self)
            return await self._outcome
        finally:
            bus.withdraw(POINT_SPOKEN_REPLY, self._spoken_reply)
            bus.unsubscribe(OUTPUT_SPEECH, self._speech)
            bus.unsubscribe(UI_BUTTONS, self._offered)
            bus.unsubscribe(OUTPUT_TEXT, self._said)
            bus.unsubscribe(OUTPUT_ERROR, self._failed)

    def _input(self) -> Message:
        return Message(
            type=INPUT_TEXT, body={"text": self.text}, username=self.username, project_id=self.project_id,
            session_id=self.session_id, channel=self.channel, origin_id=self.origin_id,
        )

    async def _said(self, message: Message) -> None:
        """A whole message. The one published after the choices is the
        answer, and the answer is what says the exchange is over; an
        earlier one is what the state owed before it could answer."""
        for body in self._mine(message):
            said = self.db.get_message(body.get("assistant_message_id")) or {}
            self.said.append({
                "id": body.get("assistant_message_id"), "content": body.get("text") or "",
                # This channel's own business, read where it has always
                # been read: the persisted row (see whatsapp/outbound.py).
                "audio_text": said.get("audio_text"),
            })
            for _ in filter(None, [self.offered]):
                self._settle(TurnOutcome(messages=list(self.said), manual_actions=self.actions))

    async def _offered(self, message: Message) -> None:
        for body in self._mine(message):
            self.offered = True
            self.actions = body.get("actions") or None

    async def _failed(self, message: Message) -> None:
        for body in self._mine(message):
            self._settle(TurnOutcome(messages=list(self.said), code=body.get("code", "")))

    async def _speech(self, message: Message) -> None:
        for body in self._mine(message):
            self.voice.announced(str(body.get("text") or ""))

    def _spoken_reply(self, spoken) -> None:
        for _ in filter(None, [spoken.session_id == self.session_id]):
            self.voice.spoken_reply(spoken)

    async def bounced(self, message: Message) -> None:
        logger.error(
            "WhatsApp: nothing listens for %s — session %s cannot run a turn here.",
            message.type, self.session_id,
        )
        self._settle(TurnOutcome(messages=[], code=NO_TURN_LISTENER))

    def _mine(self, message: Message) -> list:
        return [message.body for _ in filter(None, [message.session_id == self.session_id])]

    def _settle(self, outcome: TurnOutcome) -> None:
        for _ in filter(lambda done: not done, [self._outcome.done()]):
            self._outcome.set_result(outcome)
