"""TrackingProcessorAfterUserMessage's regeneration call (fired when the
optimistic first guess turns out to have transitioned the automaton)
must not re-request `signals` — they're already known from the first
call, and re-requesting them is wasted.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from tracking.tracking_processor import UserVariables
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_NAME = "proj"


def _automaton() -> Automaton:
    mood = Signal(name="mood", ui_label="Mood", definition="0-100 mood score.")
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50")
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


class RecordingSchemaAiService:
    """Schema-capable fake that records the schema dict passed to
    generate_stream_with_metadata on every call, and fires a
    transition-triggering signals value on the first call only."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        self.calls.append(dict(schema))
        if len(self.calls) == 1:
            on_metadata("signals", '{"mood": 80}')
            yield "draft "
        else:
            yield "final "


def _user_variables(automaton: Automaton, session_id: int) -> UserVariables:
    return UserVariables(automaton=automaton, state=automaton.states["a"], project_name=PROJECT_NAME, session_id=session_id)


async def test_regeneration_call_does_not_request_signals(db):
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    automaton = _automaton()
    ai_service = RecordingSchemaAiService()
    get_username = lambda: USERNAME
    get_active_project_name = lambda: PROJECT_NAME
    metrics = MetricService(db, get_username=get_username, get_active_project_name=get_active_project_name)
    env = PersistedEnv(db, get_username=get_username, get_active_project_name=get_active_project_name)
    scope_builder = EvaluationScopeBuilder(
        env, metrics, SystemFacts(), SessionFacts(db, get_username, get_active_project_name)
    )

    processor = TrackingProcessorAfterUserMessage(
        ai_service, scope_builder, env, db, _user_variables(automaton, session_id)
    )
    await processor.process("hello")

    assert len(ai_service.calls) == 2, "expected an optimistic call plus one regeneration"
    assert "signals" in ai_service.calls[0], "the first (detecting) call must still request signals"
    assert "signals" not in ai_service.calls[1], "the regeneration call must not re-request signals"
