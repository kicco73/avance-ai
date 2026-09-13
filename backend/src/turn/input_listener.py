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
from dataclasses import dataclass, field, replace
from itertools import takewhile

from db import Db
from system import bus
from system.bus import (
    INPUT_TEXT, INPUT_BUTTON, INPUT_REACTION, OUTPUT_ERROR, SESSION_BLOCKED,
    SESSION_ENTER, SESSION_CREATE, SESSION_OPENED, SESSION_RECALL,
    SESSION_TERMINATE, SESSION_SPEAK, Message,
)
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from system.web_session import WebSession
from turn.outbound import Outbound
from turn.turn_service import TurnService

logger = LoggerFactory.get_logger(__name__)

#: What a sender with no user row gets: the bottom of the ladder in
#: auth/roles.py, never nothing.
_LEAST_PRIVILEGED = "pending"

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
        # Entering a conversation names a project, not a session, so it
        # never goes near the queue, which is keyed by session. It
        # answers, and says so; what a conversation opens with is a
        # reaction to that.
        bus.subscribe(SESSION_ENTER, self._entering)
        bus.subscribe(SESSION_CREATE, self._entering)
        bus.subscribe(SESSION_RECALL, self._recalled)
        bus.subscribe(SESSION_TERMINATE, self._terminated)
        bus.subscribe(SESSION_SPEAK, self._speaking)
        bus.subscribe(INPUT_REACTION, self._reacted)

    async def _entering(self, message: Message) -> None:
        """Into a conversation, named the way whoever is asking knows it.

        A chat knows the project and asks for its conversation there; an
        operator has been handed one session and nothing else, and a
        person browsing past conversations picks one. Both are entering,
        and the envelope already carries either name."""
        role = self._role_of(message.username)
        kind = str((message.body or {}).get("session_type") or "live")
        with WebSession().for_sender(message.username, role=role, channel=message.channel):
            entering = Outbound(message)
            try:
                session = await self._resolved(message, kind)
            except ServiceError as exc:
                entering.failed(exc, [])
                await entering.flush()
                return
            refusal = self._refusal_of(session)
            if refusal is not None:
                entering.put(SESSION_BLOCKED, {**refusal, "session_type": kind})
                await entering.flush()
                return
            entered = replace(message, session_id=session["id"])
            said = self._turn_service.read_history(session["id"])
            outbound = Outbound(entered)
            outbound.informed(session, self._turn_service.services_for(session["id"]), kind)
            outbound.recalled(said)
            outbound.offered(self._turn_service.buttons_for(session["id"], session["state"]))
            await outbound.flush()
        # Only a conversation with nothing in it has just been opened.
        # The transcript is already in hand, and it is the whole answer:
        # anyone hearing this is told a fact, not asked to work one out.
        for _ in filter(None, [not said]):
            await bus.publish(replace(entered, type=SESSION_OPENED, body={}))

    async def _resolved(self, message: Message, kind: str) -> dict:
        for session_id in filter(None, [None if message.type == SESSION_CREATE else message.session_id]):
            return self._turn_service.session_named(session_id)
        if message.type == SESSION_CREATE:
            return await self._turn_service.create_session_of(message.project_id, kind)
        return await self._turn_service.enter_session(message.project_id, kind)

    @staticmethod
    def _refusal_of(session: dict) -> dict | None:
        for reason in filter(None, [session.get("blocked")]):
            return {"reason": reason, "detail": session.get("detail") or ""}
        for _ in filter(None, [session.get("legal_terms_pending")]):
            return {"reason": "terms", "detail": session.get("project_id") or ""}
        for _ in filter(None, [session.get("paused")]):
            return {"reason": "paused", "detail": session.get("paused_reason") or ""}
        return None

    async def _recalled(self, message: Message) -> None:
        await self._answering(message, lambda outbound: outbound.recalled(
            self._turn_service.read_history(message.session_id, (message.body or {}).get("limit")),
        ))

    async def _terminated(self, message: Message) -> None:
        await self._answering(message, lambda outbound: None, self._turn_service.close_session)

    async def _speaking(self, message: Message) -> None:
        enabled = bool((message.body or {}).get("enabled"))
        self._turn_service.set_audio_enabled(message.session_id, enabled)

    async def _reacted(self, message: Message) -> None:
        body = message.body or {}
        self._turn_service.set_message_reaction(body.get("assistant_message_id"), body.get("reaction"))

    async def _answering(self, message: Message, say, first=None) -> None:
        role = self._role_of(message.username)
        with WebSession().for_sender(message.username, role=role, channel=message.channel):
            outbound = Outbound(message)
            try:
                if first is not None:
                    await first(message.session_id)
                say(outbound)
            except ServiceError as exc:
                outbound.failed(exc, [])
            await outbound.flush()

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
            accepted = await self._accept(message)
            if accepted is None:
                return
            requests = self._requests.setdefault(message.session_id, _Requests())
            requests.add(accepted)
            if requests.answering:
                return
            requests.answering = True
        # Not awaited: an answer is written for as long as the model
        # takes, and bus.publish awaits each listener in order — awaiting
        # here would hold up whoever published the request.
        task = asyncio.create_task(self._answer(message.session_id, requests, role))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    async def _accept(self, message: Message) -> _Accepted | None:
        """Persists what the person said, right now. None when it was
        refused — that request is answered with the refusal, never joins
        the ones waiting, and opens no queue of its own."""
        outbound = Outbound(message)
        for _ in filter(None, [not isinstance(message.session_id, int)]):
            outbound.failed(ServiceError("Session not found.", status_code=404, code="session_not_found"), [])
            await outbound.flush()
            return None
        for _ in filter(INPUT_BUTTON.__eq__, [message.type]):
            # Nothing to persist: a choice taken is recorded by the
            # automaton itself.
            return _Accepted(message)
        text = str((message.body or {}).get("text") or "").strip()
        prepared: list[dict] = []
        try:
            if not text:
                raise ServiceError("Message cannot be empty.", status_code=400, code="empty_message")
            prepared = await self._turn_service.prepare_user_initiated_turn(message.session_id)
            message_id = self._turn_service.accept_user_message(message.session_id, text)
        except ServiceError as exc:
            # Whatever the state owed was written before the refusal and
            # is owed either way.
            outbound.failed(exc, prepared)
            await outbound.flush()
            return None
        return _Accepted(message, message_id, prepared)

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
            outbound = Outbound(message)
            drain = asyncio.create_task(outbound.drain())
            try:
                await self._turn(message, accepted, prepared, outbound)
            finally:
                outbound.close()
                await drain

    async def _turn(
        self, message: Message, accepted: list[int], prepared: list[dict], outbound: "Outbound",
    ) -> None:
        for _ in filter(INPUT_BUTTON.__eq__, [message.type]):
            await self._take_action(message, outbound)
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
            outbound.offered(result.get("buttons"))
            outbound.said(result["reply"])
        except ServiceError as exc:
            outbound.failed(exc, prepared)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error while processing a turn: %s", exc)
            outbound.said(prepared)
            outbound.put(OUTPUT_ERROR, {"message": "Unexpected server error.", "detail": str(exc)})


    async def _take_action(self, message: Message, outbound: "Outbound") -> None:
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
        outbound.offered(result.get("buttons"))
        outbound.said(result["reply"])


@dataclass
class _Accepted(object):
    """One request that got past every check, and what accepting it
    produced: the id it was persisted under — none for a choice taken,
    which persists nothing — and whatever the state owed before it."""
    message: Message
    message_id: int | None = None
    prepared: list[dict] = field(default_factory=list)


class _Requests(object):
    """What one session has been asked and has not been answered yet."""

    def __init__(self) -> None:
        self.waiting: list[Message] = []
        self.accepted: list[int] = []
        self.prepared: list[dict] = []
        self.answering = False

    def add(self, accepted: _Accepted) -> None:
        self.waiting.append(accepted.message)
        self.accepted.append(accepted.message_id)
        self.prepared.extend(accepted.prepared)

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


