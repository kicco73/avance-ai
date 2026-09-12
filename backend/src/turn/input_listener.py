"""What a channel's `input.text` becomes: a turn, run here.

A channel does not run turns. It posts what a person said and delivers
what comes back, and it is this — core — that listens, prepares whatever
the session needs before a turn can run, runs the turn, and publishes
every frame the turn produces. The chat window used to do all of it, in
the socket adapter this replaces, and the phone channel did it again its
own way; they did not just duplicate the work, they disagreed about it.

The preparation is part of that (see TurnService.prepare_user_initiated_turn):
the one message a state that takes no messages will ever say. It is in no
turn's reply and it happens even when the turn is then refused, so the
terminal frame is the only thing that can carry it — `prepared`, on both
"turn.ended" and "turn.failed".

On a key of its own, never folded into `reply`. `reply` is the turn's own
message and readers address it positionally: chatStoreFactory.js
reconciles the bubble it streamed against `reply[0]`, so anything placed
in front of it reconciles that bubble against the wrong message.

Nothing here names a channel. The message says which one it came from,
and the frames it publishes carry the same correlation back out —
`origin_id` for the connection that is owed the answer, `stream_id` for
the one exchange — so a channel picks out its own without core knowing
who is listening.
"""
from __future__ import annotations

import asyncio

from db import Db
from system import bus
from system.bus import (
    INPUT_TEXT, OUTPUT_SPEECH, OUTPUT_TEXT, TURN_ENDED, TURN_FAILED, TURN_STARTED, TURN_TOOL, Message,
)
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from system.web_session import WebSession
from turn.tool_status_text import tool_status_text
from turn.turn_service import TurnService

logger = LoggerFactory.get_logger(__name__)

#: What a sender with no user row gets: the bottom of the ladder in
#: auth/roles.py, never nothing.
_LEAST_PRIVILEGED = "pending"

#: Ends the drain below. Not a Message: a sentinel a producer could
#: never publish by accident.
_DONE = object()


class TurnInput(object):

    def __init__(self, turn_service: TurnService, db: Db) -> None:
        self._turn_service = turn_service
        self._db = db
        self._turns: set[asyncio.Task] = set()

    def register(self) -> None:
        bus.subscribe(INPUT_TEXT, self._start_turn)

    async def _start_turn(self, message: Message) -> None:
        # Not awaited: a turn streams for as long as the model takes, and
        # bus.publish awaits each listener in order — awaiting here would
        # hold up whoever published the message for the whole reply.
        task = asyncio.create_task(self._run(message, self._role_of(message.username)))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    def _role_of(self, username: str) -> str:
        """The sender's privileges, looked up rather than taken off the
        wire — a channel must not be able to declare its own caller's.

        A sender with no row at all is the least privileged there is, not
        a reason to drop the message: whoever sent it is owed an answer,
        and refusing to run the turn silently is the one failure a
        channel cannot report. The turn fails on its own ownership check
        instead, which says what happened."""
        user = self._db.get_user_by_id(username)
        return user["role"] if user is not None else _LEAST_PRIVILEGED

    async def _run(self, message: Message, role: str) -> None:
        with WebSession().for_sender(message.username, role=role, channel=message.channel):
            outbound = _Outbound(message)
            drain = asyncio.create_task(outbound.drain())
            try:
                await self._turn(message, outbound)
            finally:
                outbound.close()
                await drain

    async def _turn(self, message: Message, outbound: "_Outbound") -> None:
        session_id = message.session_id
        text = str(message.body or "").strip()
        prepared: list[dict] = []
        try:
            if not isinstance(session_id, int):
                raise ServiceError("Session not found.", status_code=404, code="session_not_found")
            if not text:
                raise ServiceError("Message cannot be empty.", status_code=400, code="empty_message")
            prepared = await self._turn_service.prepare_user_initiated_turn(session_id)
            user_message_id = self._turn_service.accept_user_message(session_id, text)
        except ServiceError as exc:
            outbound.failed(exc, prepared)
            return

        try:
            result = await self._turn_service.process_turn(
                session_id, text, on_metadata=outbound.on_metadata, user_message_id=user_message_id,
            )
            outbound.put(TURN_ENDED, {**result, "prepared": prepared})
        except ServiceError as exc:
            outbound.failed(exc, prepared)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error while processing a turn: %s", exc)
            outbound.put(TURN_FAILED, {
                "message": "Unexpected server error.", "detail": str(exc), "prepared": prepared,
            })


class _Outbound(object):
    """Everything a turn produces, published in the order it produced it.

    on_metadata is synchronous and is called from deep inside the turn,
    where there is nothing to await on; publishing is not. A task per
    frame would put them on the wire in whatever order the loop got to
    them, which for a stream of chunks is the one thing that must not
    happen. So the frames are queued as they are made — synchronously,
    in order — and one task drains the queue, awaiting each publish
    before it takes the next.

    Every frame is addressed the way the message that started the turn
    was: same username, session, connection and stream. That is what
    lets a channel recognise its own answer.
    """

    def __init__(self, message: Message) -> None:
        self._message = message
        self._queue: asyncio.Queue = asyncio.Queue()

    def put(self, type: str, body) -> None:
        self._queue.put_nowait(Message(
            type=type, body=body, username=self._message.username,
            project_id=self._message.project_id, session_id=self._message.session_id,
            channel=self._message.channel, origin_id=self._message.origin_id,
            stream_id=self._message.stream_id,
        ))

    def close(self) -> None:
        self._queue.put_nowait(_DONE)

    async def drain(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _DONE:
                return
            try:
                await bus.publish(item)
            except Exception as exc:  # noqa: BLE001
                # One frame nobody could take must not strand the rest:
                # a turn that stops publishing mid-stream leaves whoever
                # is listening waiting for an end that never comes.
                logger.exception("Publishing %s failed: %s", item.type, exc)

    def failed(self, exc: ServiceError, prepared: list[dict]) -> None:
        body = {
            "message": exc.message, "detail": getattr(exc, "detail", str(exc)), "prepared": prepared,
        }
        if exc.code is not None:
            body["code"] = exc.code
        self.put(TURN_FAILED, body)

    def on_metadata(self, key: str, value) -> None:
        if key == "audio":
            self.put(OUTPUT_SPEECH, str(value))
        elif key == "chunk":
            self.put(OUTPUT_TEXT, value)
        elif key == "typing":
            # A live "someone is composing a reply" signal — sent once,
            # right before real generation starts for the model (see
            # TrackingProcessor.process), or only once an operator's own
            # human_typing frame arrives for a human-answered turn.
            self.put(TURN_STARTED, {"session_id": self._message.session_id})
        elif key == "tool":
            # One frame type for both phases — a reader tells them apart
            # by phase. status_text is only ever meaningful on "start"
            # (see tool_status_text); "result" carries the payload as it
            # is.
            self.put(TURN_TOOL, {**value, "status_text": tool_status_text(value)} if value["phase"] == "start" else value)
