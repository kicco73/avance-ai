"""A mid-turn transition's regenerated reply (see TrackingProcessorAfter
UserMessage._get_ai_reply's own "transitioned" branch) is generated *for*
the target state, not the one the turn started in — so its own
`history-cutoff` (see automaton.State.history_cutoff), like its own
`output`, must be the one that governs what history that call actually
sees. Using the turn's start state instead silently ignores a
history-cutoff the target state declares, e.g. one meant to keep an
earlier state's own conversation (a different persona, a different
topic) out of a state that must not carry it forward.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from db.models import Tracking
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_variables import UserVariables
from ai.turn.tracking_processor_user import TrackingProcessorAfterUserMessage
from tracking.user_facts import UserFacts
from turn.turn_transaction import TurnTransaction

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


def _automaton() -> Automaton:
    mood = Signal(name="mood", ui_label="Mood", definition="mood")
    advance = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="signal.mood >= 50")
    state_a = State(input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[advance], history_cutoff=False)
    state_b = State(input_processor="ai", key="b", ui_label="B", final=True, contextual_prompt="there", history_cutoff=True)
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="", signals=[mood], general_attachments=(), autotracking_on_ai_message=False,
    )


def _contents(history: list[dict]) -> list[str]:
    return [str(entry.get("content")) for entry in history]


class RecordingAiService:
    def __init__(self) -> None:
        self.histories: list[list[dict]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.histories.append(history)
        if len(self.histories) == 1:
            on_metadata("signals", '{"mood": 80}')
            yield "draft "
        else:
            yield "final "


async def test_regeneration_after_a_mid_turn_transition_uses_the_target_states_own_history_cutoff(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1, 9, 0, 0), datetime_end=datetime(2026, 1, 1, 9, 0, 0),
        start_state="a", end_state="a",
    )
    db.save_message("assistant", "OLD LINE FROM BEFORE A", session_id, timestamp=datetime(2026, 1, 1, 9, 0, 0))
    Tracking.create(session=session_id, old_state="", new_state="a", timestamp=datetime(2026, 1, 1, 9, 0, 1))
    db.save_message("assistant", "LINE SAID WHILE IN A", session_id, timestamp=datetime(2026, 1, 1, 9, 0, 2))

    automaton = _automaton()
    context = FixedProjectContext(project_id=PROJECT_ID)
    env = PersistedEnv(db, context, session_id)
    ai_service = RecordingAiService()
    scope_builder = EvaluationScopeBuilder(env, MetricService(db, context), SessionFacts(db, context), UserFacts(db), db)
    processor = TrackingProcessorAfterUserMessage(
        ai_service, scope_builder, env, TurnTransaction(db, session_id, []),
        UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id),
    )

    await processor.process("hello")

    assert len(ai_service.histories) == 2
    assert "OLD LINE FROM BEFORE A" in _contents(ai_service.histories[0])
    assert "OLD LINE FROM BEFORE A" not in _contents(ai_service.histories[1])
    assert "LINE SAID WHILE IN A" in _contents(ai_service.histories[1])
