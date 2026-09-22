"""`ai-memory-scope: none` (the new default) — the memory channel is never
in the schema an ai-provider call is offered, and nothing a reply might
still report under it (a misbehaving provider is not bound by the schema
it was given) ever lands in any store.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_variables import UserVariables
from ai.turn.tracking_processor_ai import TrackingProcessorAfterAiMessage
from tracking.user_facts import UserFacts
from turn.turn_transaction import TurnTransaction

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


def _automaton() -> Automaton:
    state_a = State(input_processor="ai", key="a", ui_label="A", final=True, contextual_prompt="hi", ai_memory_scope="none")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="", signals=[], general_attachments={}, autotracking_on_ai_message=True,
    )


class RogueAiService:
    """Reports a `memory` delta even though the schema it was handed
    never offered that field — a schema-constrained real provider
    couldn't do this; a fake standing in for a misbehaving one can."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.calls.append(dict(schema))
        on_metadata("memory", "smuggled: note")
        yield "reply "


async def test_a_none_scope_state_never_offers_the_memory_channel_and_never_merges_a_reported_delta(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(), start_state="a", end_state="a",
    )
    automaton = _automaton()
    context = FixedProjectContext(project_id=PROJECT_ID)
    env = PersistedEnv(db, context, session_id)
    ai_service = RogueAiService()
    scope_builder = EvaluationScopeBuilder(env, MetricService(db, context), SessionFacts(db, context), UserFacts(db), db)
    processor = TrackingProcessorAfterAiMessage(
        ai_service, scope_builder, env, TurnTransaction(db, session_id, []),
        UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id),
    )

    await processor.process([])

    assert "memory" not in ai_service.calls[0]
    assert env.memory() == {}
    assert db.get_local_memory(session_id) == {}
