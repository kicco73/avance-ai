"""The chat window, as a thing that delivers rather than a thing that
answers.

One object, built once by the skill. Core listens for `input.text` and
publishes every frame a turn produces (see turn/input_listener.py), and
this forwards the ones addressed to a connection it holds — `origin_id`,
put there by system.bus_channel, which this package also tells which
channel it speaks on.

It owns no HTTP surface: a conversation lives on the bus, so the
routes and the thing that serves them are packaged together.

It also owns the human-takeover seam. `HumanTalker` reaches a person
through the connection they already have open, which is a chat
interface's job and no one else's: without this package TrackingService
is never given a factory, and a state that hands a turn to a person
finds nobody to hand it to.
"""
from __future__ import annotations


from system import bus
from system.bus import (
    OUTPUT_REACTION, OUTPUT_TEXT, OUTPUT_SPEECH, OUTPUT_TEXT_STREAM, OUTPUT_TOOL,
    STATE_CHANGED, STATE_BUTTONS, OUTPUT_ERROR,
    SESSION_INFO, SESSION_MESSAGES, SESSION_BLOCKED, SESSION_ENDED,
    POINT_SPOKEN_REPLY, SESSION_OPENED, Message,
)
from system.logging_factory import LoggerFactory
from system.bus_channel import BusChannel
from system.service_error import ServiceError
from talker import HumanTalker
from project.project_service import ProjectService
from turn.outbound import publishing
from turn.turn_service import TurnService

from .bus_human_relay import BusHumanRelay

logger = LoggerFactory.get_logger(__name__)

#: What a turn produces, and what this forwards. Not CLIENT_INJECTABLE's
#: mirror image: that is what a browser may put *on* the Bus, and this is
#: what comes back.
TURN_FORWARDED = (
    OUTPUT_TEXT_STREAM, OUTPUT_TEXT, OUTPUT_SPEECH, OUTPUT_TOOL, OUTPUT_REACTION,
    STATE_BUTTONS, STATE_CHANGED, OUTPUT_ERROR,
    SESSION_INFO, SESSION_MESSAGES, SESSION_BLOCKED, SESSION_ENDED,
)


class WebchatService:

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService,
        notifications: BusChannel, db,
    ) -> None:
        self._turn_service = turn_service
        self._notifications = notifications
        self._db = db

    def register(self) -> None:
        for message_type in TURN_FORWARDED:
            bus.subscribe(message_type, self._forward)
        bus.subscribe(SESSION_OPENED, self._opened)
        bus.contribute(POINT_SPOKEN_REPLY, self._spoken_reply)

    async def _opened(self, message: Message) -> None:
        async with publishing(message, self._db) as outbound:
            try:
                opened = await self._turn_service.open_conversation(message.session_id, outbound.on_metadata)
            except ServiceError as exc:
                outbound.failed(exc, [])
                return
            outbound.ran(opened)

    def _spoken_reply(self, spoken) -> None:
        for _ in filter(self._turn_service.is_audio_enabled, filter(None, [spoken.session_id])):
            spoken.want()

    async def _forward(self, message: Message) -> None:
        """One frame of an answer, onto the connection that is owed it.

        Only what a connection *here* is waiting for. An `input.text`
        converted from a voice note on another channel (see
        listen.decoder) carries an `origin_id` too — the id of the
        message it was converted from — so the test is whether that id
        names a connection this socket actually has open, not whether it
        is set.

        The wire carries the Bus's own type names and field names:
        nothing is translated on the way out, so a listener and a browser
        read the same message. A frame is the message's own body with its
        type on it — every body is a dict, so there is one shape and no
        wrapping.

        Two deliveries, one rule each: an answer goes back to whoever
        asked, and an announcement — a session closed from elsewhere, a
        conversation handed to a person — goes to whoever is showing that
        conversation. The second has no request behind it and so no
        `origin_id` to answer to."""
        frame = {"type": message.type, "session_id": message.session_id, **(message.body or {})}
        for project_id in filter(None, [message.project_id]):
            frame.setdefault("project_id", project_id)
        connection_id = message.origin_id
        if connection_id is None or not self._notifications.has_connection(connection_id):
            for session_id in filter(None, [message.session_id]):
                self._notifications.send_to_watchers(session_id, frame)
            return
        self._notifications.send_to_connection(connection_id, frame)
        for _ in filter(SESSION_INFO.__eq__, [message.type]):
            self._notifications.watch_session(connection_id, message.session_id)

    def human_talker_factory(
        self, username: str, session_id: int, session_type: str, project_id: str,
    ) -> HumanTalker:
        return HumanTalker(
            BusHumanRelay(
                self._notifications, username, session_id,
                session_type=session_type, project_id=project_id,
            )
        )
