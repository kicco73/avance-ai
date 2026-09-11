"""The chat window, as a thing that delivers rather than a thing that
answers.

One object, built once by the skill. It runs no turns: core listens for
`input.text` and publishes every frame a turn produces (see
turn/input_listener.py), and this forwards the ones addressed to a
connection it holds — `origin_id`, put there by system.bus_channel,
which this package also tells which channel it speaks on.

It owns the chat window's HTTP surface too (WebchatController), so the
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
    OUTPUT_SPEECH, OUTPUT_TEXT, TURN_ENDED, TURN_FAILED, TURN_STARTED, TURN_TOOL, Message,
)
from system.logging_factory import LoggerFactory
from system.wiring import construct
from system.bus_channel import BusChannel
from talker import HumanTalker
from project.project_service import ProjectService
from turn.turn_service import TurnService

from .webchat_controller import WebchatController
from .bus_human_relay import BusHumanRelay

logger = LoggerFactory.get_logger(__name__)

#: What a turn produces, and what this forwards. Not CLIENT_INJECTABLE's
#: mirror image: that is what a browser may put *on* the Bus, and this is
#: what comes back.
TURN_FORWARDED = (TURN_STARTED, OUTPUT_TEXT, OUTPUT_SPEECH, TURN_TOOL, TURN_ENDED, TURN_FAILED)


class WebchatService:

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService,
        notifications: BusChannel,
    ) -> None:
        self._turn_service = turn_service
        self._notifications = notifications
        # The /api/skills/webchat/* routes travel with the service that answers
        # them: the skill hands this to POINT_HTTP_CONTROLLERS and a
        # build without this package has nothing to register.
        self.controller = construct(WebchatController, {"turn_service": turn_service})

    def register(self) -> None:
        for message_type in TURN_FORWARDED:
            bus.subscribe(message_type, self._forward)

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
        read the same message. A dict body is the frame's own fields and
        is spread; anything else is the body and travels as one."""
        connection_id = message.origin_id
        if connection_id is None or not self._notifications.has_connection(connection_id):
            return
        body = message.body
        payload = dict(body) if isinstance(body, dict) else {"body": body}
        self._notifications.send_to_connection(
            connection_id, {"type": message.type, "stream_id": message.stream_id or "", **payload},
        )

    def human_talker_factory(
        self, username: str, session_id: int, session_type: str, project_id: str,
    ) -> HumanTalker:
        return HumanTalker(
            BusHumanRelay(
                self._notifications, username, session_id,
                session_type=session_type, project_id=project_id,
            )
        )
