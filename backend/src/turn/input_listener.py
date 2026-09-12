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
person is owed it either way, so it goes out as a message of its own
(`output.text`) before the answer does.

Nothing here names a channel. The message says which one it came from,
and the frames it publishes carry the same correlation back out —
`origin_id` for the connection that is owed the answer — so a channel
picks out its own without core knowing who is listening.
"""
from __future__ import annotations

import asyncio
from itertools import takewhile

from db import Db
from system import bus
from system.bus import (
    INPUT_TEXT, OUTPUT_REACTION, OUTPUT_TEXT, OUTPUT_SPEECH, OUTPUT_TEXT_STREAM, OUTPUT_TOOL,
    STATE_CHANGED, OUTPUT_ERROR, INPUT_BUTTON, SESSION_NEW, UI_BUTTONS, Message,
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
        #: session -> the requests accepted for it and not yet answered.
        self._requests: dict[int, _Requests] = {}

    def register(self) -> None:
        bus.subscribe(INPUT_TEXT, self._requested)
        # A button taken is a request too, and goes into the same queue:
        # it must not move the conversation in the middle of an answer.
        bus.subscribe(INPUT_BUTTON, self._requested)
        # Opening a conversation is an occasion for the automaton to
        # speak first, and it queues like everything else.
        bus.subscribe(SESSION_NEW, self._requested)

    async def _requested(self, message: Message) -> None:
        """One request, accepted here and answered with whatever else has
        piled up for the same session.

        Accepting is immediate and in arrival order — that is what fixes
        the order of the conversation, and it is the one thing that may
        not wait. Answering is not: while an answer is being written, the
        requests that arrive are kept, and the next answer is written for
        all of them at once. Three messages sent in a breath are two
        exchanges, not three, and nothing downstream has to recognise an
        exchange that produces nothing.
        """
        role = self._role_of(message.username)
        with WebSession().for_sender(message.username, role=role, channel=message.channel):
            requests = self._requests.setdefault(message.session_id, _Requests())
            if not await self._accept(message, requests):
                return
            if requests.answering:
                return
            requests.answering = True
        # Not awaited: an answer is written for as long as the model
        # takes, and bus.publish awaits each listener in order — awaiting
        # here would hold up whoever published the request.
        task = asyncio.create_task(self._answer(message.session_id, requests, role))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    async def _accept(self, message: Message, requests: "_Requests") -> bool:
        """Persists what the person said, right now. False when it was
        refused — that request is answered with the refusal and never
        joins the ones waiting."""
        outbound = _Outbound(message)
        for _ in filter(lambda kind: kind in (INPUT_BUTTON, SESSION_NEW), [message.type]):
            # Nothing to persist: a choice taken is recorded by the
            # automaton itself, and opening a conversation says nothing.
            requests.waiting.append(message)
            requests.accepted.append(None)
            return True
        text = str((message.body or {}).get("text") or "").strip()
        prepared: list[dict] = []
        try:
            if not isinstance(message.session_id, int):
                raise ServiceError("Session not found.", status_code=404, code="session_not_found")
            if not text:
                raise ServiceError("Message cannot be empty.", status_code=400, code="empty_message")
            prepared = await self._turn_service.prepare_user_initiated_turn(message.session_id)
            accepted = self._turn_service.accept_user_message(message.session_id, text)
        except ServiceError as exc:
            # Whatever the state owed was written before the refusal and
            # is owed either way.
            outbound.failed(exc, prepared)
            await outbound.flush()
            return False
        requests.waiting.append(message)
        requests.accepted.append(accepted)
        requests.prepared.extend(prepared)
        return True

    async def _answer(self, session_id: int, requests: "_Requests", role: str) -> None:
        try:
            while requests.waiting:
                batch, accepted, prepared = requests.take()
                # Addressed the way the last request of the batch was: it
                # is the most recent thing the person is looking at.
                await self._run(batch[-1], accepted, prepared, role)
        finally:
            requests.answering = False
            for _ in filter(None, [not requests.waiting]):
                self._requests.pop(session_id, None)

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

    async def _run(self, message: Message, accepted: list[int], prepared: list[dict], role: str) -> None:
        with WebSession().for_sender(message.username, role=role, channel=message.channel):
            outbound = _Outbound(message)
            drain = asyncio.create_task(outbound.drain())
            try:
                await self._turn(message, accepted, prepared, outbound)
            finally:
                outbound.close()
                await drain

    async def _turn(
        self, message: Message, accepted: list[int], prepared: list[dict], outbound: "_Outbound",
    ) -> None:
        for _ in filter(INPUT_BUTTON.__eq__, [message.type]):
            await self._take_action(message, outbound)
            return
        for _ in filter(SESSION_NEW.__eq__, [message.type]):
            await self._open_session(message, outbound)
            return
        session_id = message.session_id
        text = str((message.body or {}).get("text") or "").strip()
        try:
            result = await self._turn_service.process_turn(
                session_id, text, on_metadata=outbound.on_metadata, user_message_ids=accepted,
            )
            # Everything the answer needs to be read goes out before the
            # answer itself: what the state owed first, the reaction to
            # what was asked, where the conversation is now, what can be
            # done next. The answer is last, and it is what says the
            # exchange is over (see docs/BUS.md).
            # What the state had prepared to say is said only if the
            # conversation is still in it: when the automaton moves before
            # the answer is written, that message belongs to the state
            # just left and never reaches the person.
            for _ in filter(None, [not result.get("moved_before_reply")]):
                outbound.said(prepared)
            outbound.reacted(result)
            outbound.moved(result)
            outbound.offered(result["state"])
            outbound.said(result["reply"])
        except ServiceError as exc:
            outbound.failed(exc, prepared)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error while processing a turn: %s", exc)
            outbound.said(prepared)
            outbound.put(OUTPUT_ERROR, {"message": "Unexpected server error.", "detail": str(exc)})


    async def _open_session(self, message: Message, outbound: "_Outbound") -> None:
        """A conversation just opened. If this state has something to say
        before anybody says anything, it is said now — as an ordinary
        message, so whoever is showing the chat has nothing special to
        do. A conversation that has already started is owed nothing, and
        this produces no message at all."""
        try:
            result = await self._turn_service.open_if_needed(message.session_id, outbound.on_metadata)
        except ServiceError as exc:
            outbound.failed(exc, [])
            return
        for opened in filter(None, [result]):
            outbound.moved(opened)
            outbound.offered(opened["state"])
            outbound.said(opened["reply"])

    async def _take_action(self, message: Message, outbound: "_Outbound") -> None:
        """One of the choices the state offered, taken. It produces what
        the new state has to say and what it offers next — the same
        messages an answer produces, because to whoever is reading there
        is no difference."""
        action = str((message.body or {}).get("id") or "")
        try:
            result = await self._turn_service.apply_manual_action(
                action, message.session_id, on_metadata=outbound.on_metadata,
            )
        except ValueError as exc:
            logger.info("Action %r refused for session %s: %s", action, message.session_id, exc)
            outbound.put(OUTPUT_ERROR, {"code": "action_unavailable", "message": str(exc), "detail": ""})
            return
        except ServiceError as exc:
            outbound.failed(exc, [])
            return
        outbound.moved({
            "state_changed": True, "state": result["state"],
            "new_state": result["state"].get("key"), "triggered_action": action,
        })
        outbound.offered(result["state"])
        outbound.said(result["reply"])


class _Requests(object):
    """What one session has been asked and has not been answered yet."""

    def __init__(self) -> None:
        self.waiting: list[Message] = []
        self.accepted: list[int] = []
        self.prepared: list[dict] = []
        self.answering = False

    def take(self) -> tuple[list[Message], list[int], list[dict]]:
        """One answer's worth: every text that piled up, answered
        together — or one of anything else, which is a single thing done
        and is never merged with another. Never empty: the caller only
        asks while something is waiting."""
        taken = len(list(takewhile(lambda m: m.type == INPUT_TEXT, self.waiting))) or 1
        batch, self.waiting = self.waiting[:taken], self.waiting[taken:]
        accepted, self.accepted = self.accepted[:taken], self.accepted[taken:]
        prepared, self.prepared = self.prepared, []
        return batch, [a for a in accepted if a is not None], prepared


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

    def offered(self, state: dict) -> None:
        """What the person may do now. A fact about the state the
        conversation is in, which is why it does not ride on whatever
        message happened to come last."""
        self.put(UI_BUTTONS, {"actions": state.get("manual_actions") or []})

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
