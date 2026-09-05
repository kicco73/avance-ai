"""TrackingProcessor threads itself through as AiService.
generate_stream_with_metadata's own `tool_abort` (see
TurnProtocolUsingSchema.generate_reply/_tool_set_kwargs) whenever a state
declares a tool_set — never a bare Callable (this codebase's own
convention: pass the owning object, call its named method). This pins the
wiring itself: the object handed to AiService really is something whose
should_abort_tools() reflects this turn's own live signals/transition
state, in both the optimistic call (before signals resolve) and the
regeneration call that follows a transition.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, Source, State
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
    mood = Signal(name="mood", ui_label="Mood", definition="0-100 mood score.")
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50")
    # Both states declare a tool source, so a tool_set (and so tool_abort)
    # is threaded through for both the optimistic call (state a) and the
    # regeneration call (state b).
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action], ai_may_read_sources=("env",))
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.", ai_may_read_sources=("env",))
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[mood],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
        sources=[Source(name="env", url="avance:env", ui_label="Env", ai_definition="The variables.")],
    )


class ToolAbortRecordingAiService:
    """Records, per call, whether a tool_abort object was handed in and
    what should_abort_tools() answers at call time — never invokes any
    actual tool machinery, since only the wiring itself is under test."""

    def __init__(self) -> None:
        self.abort_states_at_call_start: list[bool | None] = []
        self.calls = 0

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False, tool_abort=None):
        self.abort_states_at_call_start.append(tool_abort.should_abort_tools() if tool_abort is not None else None)
        self.calls += 1
        if self.calls == 1:
            on_metadata("signals", '{"mood": 80}')
            yield "draft "
        else:
            yield "final "


def _user_variables(automaton: Automaton, session_id: int) -> UserVariables:
    return UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)


async def test_tool_abort_reflects_no_transition_yet_then_a_decided_one(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    automaton = _automaton()
    ai_service = ToolAbortRecordingAiService()
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_context)
    env = PersistedEnv(db, project_context, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)

    processor = TrackingProcessorAfterUserMessage(
        ai_service, scope_builder, env, db, _user_variables(automaton, session_id)
    )
    await processor.process("hello")

    # Call 1 (optimistic, state a): started before signals resolved, so
    # should_abort_tools() must read False at that point.
    # Call 2 (regeneration, state b): starts only once the transition is
    # already decided, so should_abort_tools() must read True.
    assert ai_service.abort_states_at_call_start == [False, True]
