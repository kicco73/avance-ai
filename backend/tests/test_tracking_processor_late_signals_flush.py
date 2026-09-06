"""Regression: TrackingProcessorAfterUserMessage's optimistic-reply buffer
(see its own docstring) holds every reply chunk back until 'signals'
resolves, so a transition decided by this same turn's signals can still
discard it. The per-chunk check that flushes the buffer once resolved
(`elif self.user.state == self.out.state: ...`) only ever runs on a chunk
*after* signals_resolved flips True — but 'signals' is schema-requested
*before* 'text' for this processor (see TrackingProcessor.build_turn_prompt's
own "before" ordering), so a provider that (correctly per schema, or not)
emits every text chunk before ever reporting 'signals' leaves nothing left
for that per-chunk check to ever catch: the buffered reply sat unflushed
forever, both on the live SSE stream (no 'chunk' event) and in
self.out.reply (so the persisted assistant message came back empty too).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
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
    # A high threshold: the model's own reported signal never actually
    # fires the trigger, so this stays a same-state (non-transitioned)
    # turn — the exact case the optimistic buffer is meant to flush live,
    # not the transitioned/regenerated one.
    mood = Signal(name="mood", ui_label="Mood", definition="0-100 mood score.")
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 999")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[mood],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class LateSignalsAiService:
    """A schema-capable fake that streams every reply chunk first and only
    reports 'signals' at the very end — modeling a provider that doesn't
    honor the requested (signals-before-text) schema field order, the way
    a real model interleaving a tool call before its final answer can."""

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        yield "Hello "
        yield "world"
        on_metadata("signals", '{"mood": 10}')


async def test_a_reply_streamed_entirely_before_signals_resolve_is_not_lost(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    automaton = _automaton()
    ai_service = LateSignalsAiService()
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_context)
    env = PersistedEnv(db, project_context, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)
    user_variables = UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)
    processor = TrackingProcessorAfterUserMessage(ai_service, scope_builder, env, db, user_variables)

    live_chunks: list[str] = []
    result = await processor.process("hi", on_metadata=lambda k, v: live_chunks.append(v) if k == "chunk" else None)

    assert "".join(live_chunks) == "Hello world"
    assistant = next(m for m in db.get_messages(session_id) if m["role"] == "assistant")
    assert assistant["content"] == "Hello world"
