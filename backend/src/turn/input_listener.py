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

from automaton.choice import ChoiceSelection, parse_button_name
from db import Db
from system import bus
from system.bus import (
    INPUT_TEXT, INPUT_BUTTON, INPUT_REACTION, OUTPUT_ERROR, SESSION_BLOCKED,
    SESSION_ENTER, SESSION_CREATE, SESSION_OPENED, SESSION_RECALL,
    SESSION_TERMINATE, SESSION_SPEAK, SESSION_EXHAUSTED, Message,
)
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from turn.turn_transaction import RowHandle
from turn.outbound import Outbound, publishing
from turn.turn_service import TurnService

logger = LoggerFactory.get_logger(__name__)

class TurnInput(object):

    def __init__(self, turn_service: TurnService, db: Db) -> None:
        self._turn_service = turn_service
        self._db = db
        self._turns: set[asyncio.Task] = set()
        self._requests: dict[int, _Requests] = {}

    def register(self) -> None:
        bus.subscribe(INPUT_TEXT, self._requested)
        bus.subscribe(INPUT_BUTTON, self._requested)
        bus.subscribe(SESSION_ENTER, self._entering)
        bus.subscribe(SESSION_CREATE, self._entering)
        bus.subscribe(SESSION_RECALL, self._recalled)
        bus.subscribe(SESSION_TERMINATE, self._terminated)
        bus.subscribe(SESSION_EXHAUSTED, self._exhausted)
        bus.subscribe(SESSION_SPEAK, self._speaking)
        bus.subscribe(INPUT_REACTION, self._reacted)

    async def _entering(self, message: Message) -> None:
        """Into a conversation, named the way whoever is asking knows it.

        A chat knows the project and asks for its conversation there; an
        operator has been handed one session and nothing else, and a
        person browsing past conversations picks one. Both are entering,
        and the envelope already carries either name."""
        kind = str((message.body or {}).get("session_type") or "live")
        async with publishing(message, self._db) as entering:
            try:
                session = await self._resolved(message, kind)
            except ServiceError as exc:
                entering.failed(exc, [])
                return
            refusal = self._refusal_of(session)
            if refusal is not None:
                entering.put(SESSION_BLOCKED, {**refusal, "session_type": kind})
                return
            entered = replace(message, session_id=session["id"])
            said = self._turn_service.read_history(session["id"])
            announcement = Outbound(entered)
            announcement.informed(
                session, self._turn_service.services_for(session["id"]), kind, self._turn_service.reply_silence_seconds,
            )
            announcement.recalled(said)
            announcement.offered(self._turn_service.buttons_for(session["id"], session["state"]))
            await announcement.flush()
        for _ in filter(None, [not said]):
            await bus.publish(replace(entered, type=SESSION_OPENED, body={}))

    async def _resolved(self, message: Message, kind: str) -> dict:
        for session_id in filter(None, [None if message.type == SESSION_CREATE else message.session_id]):
            return self._turn_service.session_named(session_id)
        project_id = self._project_id(message)
        if message.type == SESSION_CREATE:
            return await self._turn_service.create_session_of(project_id, kind)
        return await self._turn_service.enter_session(project_id, kind)

    @staticmethod
    def _project_id(message: Message) -> str:
        """Every SESSION_CREATE/SESSION_ENTER message names the project
        it is entering — the one field an envelope with no session yet
        cannot do without."""
        assert message.project_id is not None
        return message.project_id

    @staticmethod
    def _session_id(message: Message) -> int:
        """`_accept` is the one place a message's session_id shows up
        unchecked; everything past it — the whole answer/turn pipeline —
        only ever runs for a message that already cleared that check."""
        assert isinstance(message.session_id, int)
        return message.session_id

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
            self._turn_service.read_history(self._session_id(message), (message.body or {}).get("limit")),
        ))

    async def _terminated(self, message: Message) -> None:
        await self._answering(message, lambda outbound: None, self._turn_service.close_session)

    async def _exhausted(self, message: Message) -> None:
        await self._turn_service.close_exhausted_session(self._session_id(message))

    async def _speaking(self, message: Message) -> None:
        enabled = bool((message.body or {}).get("enabled"))
        self._turn_service.set_audio_enabled(self._session_id(message), enabled)

    async def _reacted(self, message: Message) -> None:
        body = message.body or {}
        assistant_message_id = body.get("assistant_message_id")
        assert assistant_message_id is not None
        self._turn_service.set_message_reaction(int(assistant_message_id), body.get("reaction"))

    async def _answering(self, message: Message, say, first=None) -> None:
        async with publishing(message, self._db) as outbound:
            try:
                if first is not None:
                    await first(self._session_id(message))
                say(outbound)
            except ServiceError as exc:
                outbound.failed(exc, [])

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
        async with publishing(message, self._db) as outbound:
            accepted = await self._accept(message, outbound)
            if accepted is None:
                return
            session_id = self._session_id(message)
            requests = self._requests.setdefault(session_id, _Requests())
            requests.add(accepted)
            if requests.answering:
                return
            requests.answering = True
            task = asyncio.create_task(self._answer(session_id, requests))
            self._turns.add(task)
            task.add_done_callback(self._turns.discard)

    async def _accept(self, message: Message, outbound: "Outbound") -> _Accepted | None:
        """Persists what the person said, right now. None when it was
        refused — that request is answered with the refusal, never joins
        the ones waiting, and opens no queue of its own."""
        for _ in filter(None, [not isinstance(message.session_id, int)]):
            outbound.failed(ServiceError("Session not found.", status_code=404, code="session_not_found"), [])
            return None
        for _ in filter(INPUT_BUTTON.__eq__, [message.type]):
            return _Accepted(message)
        text = str((message.body or {}).get("text") or "").strip()
        prepared: list[dict] = []
        try:
            if not text:
                raise ServiceError("Message cannot be empty.", status_code=400, code="empty_message")
            session_id = self._session_id(message)
            prepared = await self._turn_service.prepare_user_initiated_turn(session_id)
            accepted = self._turn_service.accept_user_message(session_id, text)
        except ServiceError as exc:
            outbound.failed(exc, prepared)
            return None
        return _Accepted(message, accepted, prepared)

    async def _answer(self, session_id: int, requests: "_Requests") -> None:
        try:
            while requests.waiting:
                batch, accepted, prepared = requests.take()
                await self._run(batch[-1], accepted, prepared)
        finally:
            requests.answering = False
            for _ in filter(None, [not requests.waiting]):
                self._requests.pop(session_id, None)

    async def _run(self, message: Message, accepted: list[RowHandle], prepared: list[dict]) -> None:
        async with publishing(message, self._db) as outbound:
            await self._turn(message, accepted, prepared, outbound)

    async def _turn(
        self, message: Message, accepted: list[RowHandle], prepared: list[dict], outbound: "Outbound",
    ) -> dict | None:
        for _ in filter(INPUT_BUTTON.__eq__, [message.type]):
            return await self._take_action(message, outbound)
        session_id = self._session_id(message)
        text = str((message.body or {}).get("text") or "").strip()
        try:
            result = await self._turn_service.process_turn(
                session_id, text, on_metadata=outbound.on_metadata, user_messages=accepted,
            )
            for _ in filter(None, [not result.get("moved_before_reply")]):
                outbound.said(prepared)
            outbound.ran(result)
            return result
        except ServiceError as exc:
            outbound.failed(exc, prepared)
        except Exception:
            outbound.said(prepared)
            raise
        return None


    async def _take_action(self, message: Message, outbound: "Outbound") -> dict | None:
        """One of the choices the state offered, taken. It produces what
        the new state has to say and what it offers next — the same
        messages an answer produces, because to whoever is reading there
        is no difference."""
        action = str((message.body or {}).get("id") or "")
        try:
            selection = parse_button_name(action, self._turn_service.choice_options_for(self._session_id(message)))
        except ValueError as exc:
            logger.info("Choice %r refused for session %s: %s", action, message.session_id, exc)
            outbound.put(OUTPUT_ERROR, {"code": "choice_unavailable", "message": str(exc), "detail": ""})
            return None
        if selection is None:
            return await self._take_manual_action(action, message, outbound)
        return await self._take_choice(selection, action, message, outbound)

    async def _take_manual_action(self, action: str, message: Message, outbound: "Outbound") -> dict | None:
        try:
            result = await self._turn_service.apply_manual_action(
                action, self._session_id(message), on_metadata=outbound.on_metadata,
            )
        except ValueError as exc:
            logger.info("Action %r refused for session %s: %s", action, message.session_id, exc)
            outbound.put(OUTPUT_ERROR, {"code": "action_unavailable", "message": str(exc), "detail": ""})
            return None
        except ServiceError as exc:
            outbound.failed(exc, [])
            return None
        outbound.ran(result)
        return result

    async def _take_choice(
        self, selection: ChoiceSelection, action: str, message: Message, outbound: "Outbound",
    ) -> dict | None:
        try:
            result = await self._turn_service.apply_choice(
                selection, self._session_id(message), on_metadata=outbound.on_metadata,
            )
        except ValueError as exc:
            logger.info("Choice %r refused for session %s: %s", action, message.session_id, exc)
            outbound.put(OUTPUT_ERROR, {"code": "choice_unavailable", "message": str(exc), "detail": ""})
            return None
        except ServiceError as exc:
            outbound.failed(exc, [])
            return None
        outbound.ran(result)
        return result


@dataclass
class _Accepted(object):
    """One request that got past every check, and what accepting it
    produced: the id it was persisted under — none for a choice taken,
    which persists nothing — and whatever the state owed before it."""
    message: Message
    message_id: RowHandle | None = None
    prepared: list[dict] = field(default_factory=list)


class _Requests(object):
    """What one session has been asked and has not been answered yet."""

    def __init__(self) -> None:
        self.waiting: list[Message] = []
        self.accepted: list[RowHandle | None] = []
        self.prepared: list[dict] = []
        self.answering = False

    def add(self, accepted: _Accepted) -> None:
        self.waiting.append(accepted.message)
        self.accepted.append(accepted.message_id)
        self.prepared.extend(accepted.prepared)

    def take(self) -> tuple[list[Message], list[RowHandle], list[dict]]:
        """One answer's worth: every text that piled up, answered
        together — or one of anything else, which is a single thing done
        and is never merged with another. Never empty: the caller only
        asks while something is waiting."""
        taken = len(list(takewhile(lambda m: m.type == INPUT_TEXT, self.waiting))) or 1
        batch, self.waiting = self.waiting[:taken], self.waiting[taken:]
        accepted, self.accepted = self.accepted[:taken], self.accepted[taken:]
        prepared, self.prepared = self.prepared, []
        return batch, [a for a in accepted if a is not None], prepared


