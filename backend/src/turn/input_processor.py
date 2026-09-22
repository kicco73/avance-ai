"""Who answers in a state, and how an input reaches it.

A state declares its processor (automaton/input_processor_kind.py); this
is the runtime half, keyed the same way. `ai` answers through the model
(TrackingService); `system` answers with what the exchange's on-exit
scripts wrote through chat.write, and never runs a turn. What the two
share — the lock, the transition a button applies, the exchange, the
shape of the result — is the base class, once. What differs is `reply`.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, AsyncIterator

from automaton.automaton import Automaton, State
from automaton.choice import ChoiceSelection
from system import bus
from system.bus import POINT_INPUT_PROCESSORS
from system.web_session import WebSession
from tracking.turn_callbacks import OnMetadata
from turn.errors import TurnServiceError
from turn.turn_transaction import PendingMessage, RowHandle, TurnTransaction, row_id

if TYPE_CHECKING:
    from tracking.tracking_processor import TrackingProcessor
    from turn.turn_service import TurnService


def plain_reply_result(
    session_id: int, automaton: Automaton, state: State, assistant_message: RowHandle,
    user_messages: list[PendingMessage] | None, buttons: list[dict], ai_model: dict,
) -> dict:
    return {
        "reply": [],
        "user_message_id": (user_messages or [None])[-1],
        "user_message_reaction": None,
        "assistant_message_id": assistant_message,
        "state": automaton.get_state_payload(state),
        "buttons": buttons,
        "state_changed": False,
        "from_state": None,
        "new_state": None,
        "triggered_action": None,
        "env_changed": {},
        "ai_model": ai_model,
        "session_id": session_id,
    }


def committed(transaction: TurnTransaction, turn_result: dict) -> dict:
    assistant_message = turn_result["assistant_message_id"]
    return {
        **turn_result,
        "reply": [transaction.get_message(assistant_message)],
        "assistant_message_id": assistant_message.id,
        "user_message_id": row_id(turn_result["user_message_id"]),
    }


def fragments_of(transaction: TurnTransaction, user_messages: list[PendingMessage] | None) -> list[tuple[PendingMessage, dict]]:
    found = ((handle, transaction.get_message(handle)) for handle in user_messages or [])
    return [(handle, message) for handle, message in found if message is not None]


class InputProcessor(object):
    name: str

    def __init__(self, turns: "TurnService") -> None:
        self._turns = turns

    async def text(
        self, session_id: int, text: str | None, on_metadata: OnMetadata | None, user_messages: list[PendingMessage] | None,
    ) -> dict:
        project_id = self._turns.project_id_for_session(session_id)
        transaction = self._turns.exchange(session_id, user_messages)
        async with self._turns.session_scope(project_id, session_id), transaction:
            turn_result = await self.turn(session_id, on_metadata, user_messages, transaction)
        return committed(transaction, turn_result)

    async def manual_action(self, action_name: str, session_id: int, on_metadata: OnMetadata | None) -> dict:
        project_id = self._turns.project_id_for_session(session_id)
        self._turns.ensure_project_available(project_id)
        self._turns.refuse_while_answering(session_id)
        async with self._turns.session_scope(project_id, session_id):
            automaton, source_state = self._turns.automaton_and_state_for(session_id)
            session = self._turns.require_active_session(session_id, project_id, source_state.key)
            _, action, source_state_key = self._turns.resolve_manual_action(action_name, session["id"])
            transaction = self._turns.exchange(session["id"], [])
            tracking_engine, _ = self._turns.tracking_engine_for(session["id"], transaction)
            async with transaction:
                _, env_changed = tracking_engine.apply_transition(
                    automaton, source_state, action, None, ChoiceSelection.NONE, session["id"],
                    origin='manual', username=WebSession().user, project_id=project_id,
                )
                turn_result = await self.turn(session["id"], on_metadata, [], transaction)
            return self._transition_result(
                session["id"], source_state_key, action.name, env_changed, committed(transaction, turn_result),
            )

    async def choice(self, selection: ChoiceSelection, session_id: int, on_metadata: OnMetadata | None) -> dict | None:
        project_id = self._turns.project_id_for_session(session_id)
        self._turns.ensure_project_available(project_id)
        self._turns.refuse_while_answering(session_id)
        async with self._turns.session_scope(project_id, session_id):
            automaton, source_state = self._turns.automaton_and_state_for(session_id)
            session = self._turns.require_active_session(session_id, project_id, source_state.key)
            if selection.key not in source_state.choice_keys:
                raise ValueError(f"'{selection.key}' is not a choice offered in state '{source_state.key}'.")
            if selection.option not in self._turns.choice_options_for(session["id"]).get(selection.key, []):
                raise ValueError(f"'{selection.option}' is not among the current options of '{selection.key}'.")
            transaction = self._turns.exchange(session["id"], [])
            tracking_engine, _ = self._turns.tracking_engine_for(session["id"], transaction)
            action = tracking_engine.evaluate_choice(automaton, source_state.key, selection, session["id"])
            if action is None:
                return None
            async with transaction:
                _, env_changed = tracking_engine.apply_transition(
                    automaton, source_state, action, None, selection, session["id"],
                    origin='manual', username=WebSession().user, project_id=project_id,
                )
                turn_result = await self.turn(session["id"], on_metadata, [], transaction)
            return self._transition_result(
                session["id"], source_state.key, action.name, env_changed, committed(transaction, turn_result),
            )

    async def turn(
        self, session_id: int, on_metadata: OnMetadata | None, user_messages: list[PendingMessage] | None,
        transaction: TurnTransaction,
    ) -> dict:
        session = transaction.get_chat_session(session_id)
        if session is None:
            raise TurnServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)
        project_id = session["project_id"]
        self._turns.ensure_project_available(project_id)
        automaton, state = self._turns.automaton_and_state_or_raise_unsupported(session_id, session, transaction)
        self._turns.require_active_session(session_id, project_id, state.key)
        fragments = fragments_of(transaction, user_messages)
        reply = await self._turns.processor_for(state).reply(
            transaction, session, automaton, state, on_metadata, fragments,
        )
        self._turns.touch_session(reply['session_id'], reply['state']['key'])
        reply['buttons'] = self._turns.buttons_for(session_id, reply['state'])
        return reply

    def _transition_result(
        self, session_id: int, source_state_key: str, action_name: str, env_changed: dict, turn_result: dict,
    ) -> dict:
        _, state = self._turns.automaton_and_state_for(session_id)
        self._turns.touch_session(session_id, state.key)
        fresh = turn_result["state"]
        return {
            "state": fresh,
            "state_changed": True,
            "from_state": source_state_key,
            "new_state": fresh.get("key"),
            "triggered_action": action_name,
            "env_changed": env_changed,
            "buttons": self._turns.buttons_for(session_id, fresh),
            "reply": turn_result["reply"],
            "ai_model": self._turns.get_ai_models_info(),
            "session_id": session_id,
        }

    async def reply(
        self, transaction: TurnTransaction, session: dict, automaton: Automaton, state: State,
        on_metadata: OnMetadata | None, fragments: list[tuple[PendingMessage, dict]],
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def estimate(cls, automaton: Automaton, state: State, files) -> str:
        raise NotImplementedError

    @classmethod
    def reply_after_transition(
        cls, processor: "TrackingProcessor", state: State, on_metadata: OnMetadata,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    @classmethod
    def reply_after_answer(
        cls, processor: "TrackingProcessor", state: State, on_metadata: OnMetadata,
    ) -> AsyncIterator[str]:
        raise NotImplementedError


class SystemInputProcessor(InputProcessor):
    name = "system"

    async def reply(self, transaction, session, automaton, state, on_metadata, fragments) -> dict:
        session_id = session["id"]
        assistant_message = transaction.save_message("assistant", self._turns.outbox(session_id).take(), session_id)
        transaction.mark_messages_answered([handle for handle, _ in fragments], assistant_message)
        return plain_reply_result(
            session_id, automaton, state, assistant_message, [handle for handle, _ in fragments],
            self._turns.buttons_for(session_id, automaton.get_state_payload(state)), self._turns.get_ai_models_info(),
        )

    @classmethod
    def estimate(cls, automaton: Automaton, state: State, files) -> str:
        return ""

    @classmethod
    async def reply_after_transition(cls, processor, state, on_metadata) -> AsyncIterator[str]:
        for text in filter(None, [processor.reply_sink.take()]):
            yield text

    @classmethod
    async def reply_after_answer(cls, processor, state, on_metadata) -> AsyncIterator[str]:
        for text in filter(None, [processor.reply_sink.take()]):
            yield text


INPUT_PROCESSORS: dict[str, type[InputProcessor]] = {SystemInputProcessor.name: SystemInputProcessor}
"""What core alone can answer a state with. `ai` is not here — it is
contributed to POINT_INPUT_PROCESSORS by the ai skill's own skill.py, the
same way task.send_mail is contributed by mail's rather than named here.
A build without the ai skill still validates `input-processor: ai` at
build time (automaton/input_processor_kind.py); it just has nothing that
answers it, exactly like a build without mail still validates
task.send_mail and has nothing that sends it."""


def processors() -> dict[str, type[InputProcessor]]:
    return bus.collect(POINT_INPUT_PROCESSORS, dict(INPUT_PROCESSORS))
