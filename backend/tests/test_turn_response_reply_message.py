"""process()'s own turn response now carries the turn's persisted
assistant message in "reply" — the same {id, content, audio_text,
timestamp} shape apply_manual_action's own "reply" already sends (see
ChatService._messages_for_transition) — so a live SSE/WS turn's frontend
can reconcile its streaming bubble against the persisted row on `done`
instead of trusting every chunk to have arrived (see
chatStoreFactory.js's submitMessage). Previously this was always [].
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
from tracking.tracking_processor import UserVariables
from tracking.user_facts import UserFacts
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


def _automaton() -> Automaton:
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class PlainAiService:
    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        yield "Hello there"


async def test_reply_carries_the_persisted_assistant_message(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    automaton = _automaton()
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_context)
    env = PersistedEnv(db, project_context, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)
    user_variables = UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)
    processor = TrackingProcessorAfterUserMessage(PlainAiService(), scope_builder, env, db, user_variables)

    result = await processor.process("hi")

    assert result["assistant_message_id"] is not None
    assert len(result["reply"]) == 1
    reply_message = result["reply"][0]
    assert reply_message["id"] == result["assistant_message_id"]
    assert reply_message["content"] == "Hello there"
    assert "audio_text" in reply_message
    assert "timestamp" in reply_message
