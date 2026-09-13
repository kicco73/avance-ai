"""The first thing a conversation says, asked for by the chat window.

Core resolves which conversation you are in and announces it — which one
it is, what was said in it, what it offers — and stops there. Whether
that conversation should now speak is not a fact about the session: it is
what a chat does when it opens, and a channel that shows no chat (the
phone one opens its conversations itself) must not inherit it.

So the announcement ends with `session.opened` on the Bus and this
answers it. It is not a request and never joins the queue of requests in
turn/input_listener.py: that queue exists to fix the order of what a
person says, and nobody asked for this one.
"""
from __future__ import annotations

import asyncio

from system import bus
from system.bus import SESSION_OPENED, Message
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from system.web_session import WebSession
from turn.outbound import Outbound
from turn.turn_service import TurnService

logger = LoggerFactory.get_logger(__name__)

#: What a sender with no user row gets: the bottom of the ladder in
#: auth/roles.py, never nothing.
_LEAST_PRIVILEGED = "pending"


class ConversationOpener:

    def __init__(self, turn_service: TurnService, db) -> None:
        self._turn_service = turn_service
        self._db = db
        self._openings: set[asyncio.Task] = set()

    def register(self) -> None:
        bus.subscribe(SESSION_OPENED, self._opened)

    async def _opened(self, message: Message) -> None:
        user = self._db.get_user_by_id(message.username)
        role = user["role"] if user is not None else _LEAST_PRIVILEGED
        task = asyncio.create_task(self._speaking(message, role))
        self._openings.add(task)
        task.add_done_callback(self._openings.discard)

    async def _speaking(self, message: Message, role: str) -> None:
        """Its own task, like an answer: the announcement is already on
        the wire and must not wait for a model."""
        with WebSession().for_sender(message.username, role=role, channel=message.channel):
            outbound = Outbound(message)
            drain = asyncio.create_task(outbound.drain())
            try:
                await self._open(message, outbound)
            finally:
                outbound.close()
                await drain

    async def _open(self, message: Message, outbound: Outbound) -> None:
        """What the state has to say before anybody says anything, said
        as an ordinary message — to whoever is reading there is no
        difference between a greeting and an answer."""
        try:
            result = await self._turn_service.open_conversation(message.session_id, outbound.on_metadata)
        except ServiceError as exc:
            outbound.failed(exc, [])
            return
        for opened in filter(None, [result]):
            outbound.moved(opened)
            outbound.offered(opened.get("buttons"))
            outbound.said(opened["reply"])
