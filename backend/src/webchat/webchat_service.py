"""What listens for a person speaking, and answers on the socket they
spoke from.

One object, built once by the skill: it subscribes to every type a
client is allowed to inject (bus.CLIENT_INJECTABLE) and, for each,
starts a turn whose frames go back to the connection the message came
in on — `origin_id`, put there by system.ws_notifications.

It owns the chat window's HTTP surface too (WebchatController), so the
routes and the thing that serves them are packaged together.

It also owns the human-takeover seam. `HumanTalker` reaches a person
through the connection they already have open, which is a chat
interface's job and no one else's: without this package TrackingService
is never given a factory, and a state that hands a turn to a person
finds nobody to hand it to.
"""
from __future__ import annotations

import asyncio

from system import bus
from system.bus import CLIENT_INJECTABLE, Message
from turn.channels import NATIVE_CHAT
from system.logging_factory import LoggerFactory
from system.wiring import construct
from system.ws_notifications import WsNotifications
from talker import HumanTalker
from project.project_service import ProjectService
from turn.turn_service import TurnService

from .webchat_controller import WebchatController
from .ws_human_relay import WsHumanRelay
from .ws_turn import WsChatTurn

logger = LoggerFactory.get_logger(__name__)


class WebchatService:

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService,
        notifications: WsNotifications,
    ) -> None:
        self._turn_service = turn_service
        self._notifications = notifications
        self._turn_tasks: set[asyncio.Task] = set()
        # The /api/chat/* routes travel with the service that answers
        # them: the skill hands this to POINT_HTTP_CONTROLLERS and a
        # build without this package has nothing to register.
        self.controller = construct(WebchatController, {"turn_service": turn_service})

    def register(self) -> None:
        for message_type in CLIENT_INJECTABLE:
            bus.subscribe(message_type, self._start_turn)

    async def _start_turn(self, message: Message) -> None:
        """Only what a person said into one of *these* connections: an
        `input.text` converted from a voice note on another channel (see
        listen.decoder) carries that channel and belongs to whoever
        published the audio, never to a chat window here."""
        connection_id = message.origin_id
        if connection_id is None or message.channel != NATIVE_CHAT:
            return

        def send(payload: dict) -> None:
            self._notifications.send_to_connection(connection_id, payload)

        turn = WsChatTurn(
            self._turn_service, send, str(message.stream_id or ""),
            message.session_id, str(message.body or ""), message.username,
        )
        if not turn.accept():
            return
        # Not awaited: the turn streams for as long as the model takes,
        # and bus.publish awaits each listener in order — a turn awaited
        # here would hold the socket's read loop for the whole reply.
        task = asyncio.create_task(turn.run())
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    def human_talker_factory(
        self, username: str, session_id: int, session_type: str, project_id: str,
    ) -> HumanTalker:
        return HumanTalker(
            WsHumanRelay(
                self._notifications, username, session_id,
                session_type=session_type, project_id=project_id,
            )
        )
