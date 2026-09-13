"""Everything a turn produces, on its way out.

Here rather than inside the listener because a turn is not only ever
started by something a person typed: a channel showing a conversation
that has just opened asks for the message its state owes, and it
publishes the same frames in the same order. Whoever runs a turn owns
this; whoever decides one should run does not have to own it too.
"""
from __future__ import annotations

import asyncio

from system import bus
from system.bus import (
    OUTPUT_REACTION, OUTPUT_TEXT, OUTPUT_SPEECH, OUTPUT_TEXT_STREAM, OUTPUT_TOOL,
    STATE_CHANGED, OUTPUT_ERROR, STATE_BUTTONS, SESSION_INFO, SESSION_MESSAGES, Message,
)
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from turn.tool_status_text import tool_status_text

logger = LoggerFactory.get_logger(__name__)

#: Ends the drain below. Not a Message: a sentinel a producer could
#: never publish by accident.
_DONE = object()


class Outbound(object):
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
        ))

    def close(self) -> None:
        self._queue.put_nowait(_DONE)

    async def flush(self) -> None:
        """Publishes what is queued and stops — for a refusal, which has
        no answer to run alongside it."""
        self.close()
        await self.drain()

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

    def said(self, messages: list[dict]) -> None:
        """Every whole message this exchange produced, in the order it
        produced them: what the state owed before it could answer, then
        the answer itself. One publication each — one message is what a
        person reads, and the chunks before it were pieces of this. The
        row id travels with the text: it is how a reader ties the bubble
        it streamed, the tool trace and the audio to the row they are
        about. Named for whose message it is — a reaction names the
        person's own message, and the two must never be read as one. The
        text to be spoken is `output.speech`, published when the model
        writes it, not a field of the message."""
        for message in messages:
            self.put(OUTPUT_TEXT, {
                "text": str(message.get("content") or ""),
                "assistant_message_id": message.get("id"),
                # The server's own time for this message, not the clock of
                # whoever is showing it.
                "timestamp": message.get("timestamp"),
            })

    def reacted(self, result: dict) -> None:
        """The model's own reaction to what the person just said. It
        belongs to that message and carries its id — which is also how a
        reader learns the id of the message it just sent."""
        for reaction in filter(None, [result.get("user_message_reaction")]):
            self.put(OUTPUT_REACTION, {
                "user_message_id": result.get("user_message_id"), "reaction": reaction,
            })

    def moved(self, result: dict) -> None:
        """Where the conversation is now, said only when it moved: a
        reader keeps the last state it was told about, and a turn that
        changed nothing is not news about the state."""
        for _ in filter(None, [result.get("state_changed")]):
            self.put(STATE_CHANGED, {
                "state": result.get("state"),
                "new_state": result.get("new_state"),
                "triggered_action": result.get("triggered_action"),
            })

    def informed(self, session: dict, services: dict, kind: str) -> None:
        self.put(SESSION_INFO, {
            "state": session.get("state"),
            "services": services,
            "audio": session.get("audio", False),
            "current": session.get("current", True),
            "channel": session.get("channel"),
            "project_id": session.get("project_id"),
            "session_type": kind,
        })

    def recalled(self, messages: list[dict]) -> None:
        self.put(SESSION_MESSAGES, {"messages": messages})

    def offered(self, buttons: list[dict] | None) -> None:
        """What the person may do now. A fact about the state the
        conversation is in, which is why it does not ride on whatever
        message happened to come last — and why it is not a field of the
        state either: this message is the only place the choices are."""
        self.put(STATE_BUTTONS, {"actions": buttons or []})

    def failed(self, exc: ServiceError, prepared: list[dict]) -> None:
        """What the state owed is owed either way: it was written before
        the refusal and goes out as any other message, so the terminal
        frame carries only what went wrong."""
        self.said(prepared)
        body = {"message": exc.message, "detail": getattr(exc, "detail", str(exc))}
        if exc.code is not None:
            body["code"] = exc.code
        self.put(OUTPUT_ERROR, body)

    def on_metadata(self, key: str, value) -> None:
        if key == "audio":
            self.put(OUTPUT_SPEECH, {"text": str(value)})
        elif key == "chunk":
            self.put(OUTPUT_TEXT_STREAM, {"text": value})
        elif key == "typing":
            # The reply has started being written and none of it is
            # readable yet — an empty piece of it, sent once: right before
            # real generation starts for the model (see TrackingProcessor.
            # process), or when an operator's own human_typing frame
            # arrives for a human-answered turn.
            self.put(OUTPUT_TEXT_STREAM, {"text": ""})
        elif key == "tool":
            # One frame type for both phases — a reader tells them apart
            # by phase. status_text is only ever meaningful on "start"
            # (see tool_status_text); "result" carries the payload as it
            # is.
            self.put(OUTPUT_TOOL, {**value, "status_text": tool_status_text(value)} if value["phase"] == "start" else value)
