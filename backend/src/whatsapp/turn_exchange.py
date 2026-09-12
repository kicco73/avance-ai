"""One turn of this channel's, run by whoever listens for `input.text`.

The channel posts what the person said and waits for the frame that ends
it — `turn.ended` with the reply, or `turn.failed` with a code — picked
out of everything else on the Bus by this exchange's own `stream_id`.
Without that id the frames of a turn would come back indistinguishable
from a `task.whatsapp` message addressed to the same number, which
carries neither stream nor origin (see actuators/actuator_set.py).

`output.speech` of the same stream is the reply's spoken text, emitted
well before the rest of the reply is written: that is the moment the
voice note starts being synthesized, so by the time the turn is over it
is already encoded. AiTalker.talk publishes `output.speech` too, with no
stream_id at all, which is why the filter is on the stream and not on the
type alone.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from system import bus
from system.bus import (
    INPUT_TEXT, OUTPUT_SPEECH, POINT_SPOKEN_REPLY, TURN_ENDED, TURN_FAILED, Message,
)
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

#: What a turn nobody was listening for comes back as. Not a code any
#: ServiceError raises: no listener for `input.text` means this build has
#: nothing that runs turns at all.
NO_TURN_LISTENER = "no_turn_listener"


@dataclass(frozen=True)
class TurnOutcome:
    """What the terminal frame said, as the channel reads it: everything
    the exchange produced (whatever the preparation wrote, then the turn's
    own reply), the actions now offered, and the code of the failure when
    there was one."""

    messages: list[dict]
    manual_actions: list[dict] | None = None
    code: str | None = None


class TextReply(object):
    """An exchange whose answer is going out as text: nothing to warm up,
    and no spoken version of the reply worth asking the model for."""

    def spoken_reply(self, spoken) -> None:
        pass

    def announced(self, text: str) -> None:
        pass


class VoiceReply(object):
    """An exchange whose answer is going out as a voice note: the turn is
    asked for a spoken reply, and synthesis starts the moment it announces
    one."""

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
    stream_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    async def run(self) -> TurnOutcome:
        self._outcome: asyncio.Future[TurnOutcome] = asyncio.get_running_loop().create_future()
        bus.subscribe(TURN_ENDED, self._ended)
        bus.subscribe(TURN_FAILED, self._failed)
        bus.subscribe(OUTPUT_SPEECH, self._speech)
        bus.contribute(POINT_SPOKEN_REPLY, self._spoken_reply)
        try:
            await bus.publish_with_bounceback(self._input(), self)
            return await self._outcome
        finally:
            bus.withdraw(POINT_SPOKEN_REPLY, self._spoken_reply)
            bus.unsubscribe(OUTPUT_SPEECH, self._speech)
            bus.unsubscribe(TURN_FAILED, self._failed)
            bus.unsubscribe(TURN_ENDED, self._ended)

    def _input(self) -> Message:
        return Message(
            type=INPUT_TEXT, body=self.text, username=self.username, project_id=self.project_id,
            session_id=self.session_id, channel=self.channel, origin_id=self.origin_id,
            stream_id=self.stream_id,
        )

    async def _ended(self, message: Message) -> None:
        for body in self._mine(message):
            self._settle(TurnOutcome(
                messages=[*body["prepared"], *body["reply"]],
                manual_actions=body["state"]["manual_actions"],
            ))

    async def _failed(self, message: Message) -> None:
        for body in self._mine(message):
            self._settle(TurnOutcome(messages=list(body["prepared"]), code=body.get("code", "")))

    async def _speech(self, message: Message) -> None:
        for body in self._mine(message):
            self.voice.announced(str(body))

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
        return [message.body for _ in filter(None, [message.stream_id == self.stream_id])]

    def _settle(self, outcome: TurnOutcome) -> None:
        for _ in filter(lambda done: not done, [self._outcome.done()]):
            self._outcome.set_result(outcome)
