"""`ai-memory-strategy: clear` wipes the model's memory *as the transition
lands* — before the reply the new state generates is prompted, whichever
flow fired it. With signal tracking on the user message, the turn
regenerates its reply in the new state: that regeneration must be
prompted with the memory already cleared, or the model reads the old
notes, repeats them in its own delta, and the wipe never shows.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.prompt import EMBED_MEMORY_TAG_HEADER
from tracking.session_facts import SessionFacts
from tracking.tracking_processor import UserVariables
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


def _automaton() -> Automaton:
    mood = Signal(name="mood", ui_label="Mood", definition="mood")
    advance = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[advance])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="there", ai_memory_strategy="clear")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="", signals=[mood], general_attachments=(), autotracking_on_ai_message=False,
    )


class RecordingAiService:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.prompts.append(system_prompt.full_text() if hasattr(system_prompt, 'full_text') else system_prompt)
        if len(self.prompts) == 1:
            on_metadata("signals", '{"mood": 80}')
            yield "draft "
        else:
            on_metadata("memory", "fresh: note")
            yield "final "


def _memory_block(prompt: str) -> str:
    return prompt[prompt.index(EMBED_MEMORY_TAG_HEADER):] if EMBED_MEMORY_TAG_HEADER in prompt else ""


async def test_the_regenerated_reply_is_prompted_with_the_memory_already_cleared(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(), start_state="a", end_state="a",
    )
    automaton = _automaton()
    context = FixedProjectContext(project_id=PROJECT_ID)
    env = PersistedEnv(db, context, session_id)
    env.update({"stale": "old note"})
    ai_service = RecordingAiService()
    scope_builder = EvaluationScopeBuilder(env, MetricService(db, context), SessionFacts(db, context), UserFacts(db), db)
    processor = TrackingProcessorAfterUserMessage(
        ai_service, scope_builder, env, db,
        UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id),
    )

    await processor.process("hello")

    assert len(ai_service.prompts) == 2
    assert "old note" in _memory_block(ai_service.prompts[0])
    assert "old note" not in _memory_block(ai_service.prompts[1])
    assert env.memory() == {"fresh": "note"}
