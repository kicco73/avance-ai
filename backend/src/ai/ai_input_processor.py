"""`input-processor: ai` — a state answered through the model. The only
input processor this build has if the ai skill is installed; if it
isn't, `turn/input_processor.py`'s own `system` is what a state falls
back to (or is validated against at build time — see
automaton/input_processor_kind.py)."""
from __future__ import annotations

from typing import AsyncIterator

from automaton.automaton import Automaton, State
from turn.input_processor import InputProcessor


class AiInputProcessor(InputProcessor):
    name = "ai"

    async def reply(self, transaction, session, automaton, state, on_metadata, fragments) -> dict:
        from ai.turn.tracking_processor_ai import TrackingProcessorAfterAiMessage
        from ai.turn.tracking_processor_user import TrackingProcessorAfterUserMessage

        session_id = session["id"]
        ai_service = self._turns.ai_service_for_session_type(session["type"])
        reply = self._turns.outbox(session_id)
        tracking_service = self._turns.tracking_service
        scope_builder, env, user_vars = tracking_service.build_turn_scope(
            transaction, session_id, automaton, state, ai_service, reply,
        )
        TrackingProcessorClass = (
            TrackingProcessorAfterAiMessage if automaton.autotracking_on_ai_message else TrackingProcessorAfterUserMessage
        )
        processor = TrackingProcessorClass(
            ai_service, scope_builder, env, transaction, user_vars,
            input_token_budget_per_turn=tracking_service.get_input_token_budget_per_turn(), reply=reply,
        )
        return await processor.process(
            [m["content"] for _, m in fragments], on_metadata=on_metadata,
            user_messages=[handle for handle, _ in fragments],
        )

    @classmethod
    def estimate(cls, automaton: Automaton, state: State, files) -> str:
        from ai.turn.tracking_processor import estimate_state_prompt
        return estimate_state_prompt(automaton, state, files)

    @classmethod
    def reply_after_transition(cls, processor, state, on_metadata) -> AsyncIterator[str]:
        return processor.regenerate_reply(state, on_metadata)

    @classmethod
    async def reply_after_answer(cls, processor, state, on_metadata) -> AsyncIterator[str]:
        return
        yield

    @classmethod
    def owes_turn_on_entry(cls, state: State) -> bool:
        return state.final or not state.chat_enabled
