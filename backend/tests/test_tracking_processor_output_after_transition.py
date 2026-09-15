"""A trigger that moves the conversation mid-turn (see
tracking_processor_user.TrackingProcessorAfterUserMessage) lands on a new
state before that state has ever had a turn of its own — its own `output`
(see automaton.State.output) has never been offered to the model, only the
state the turn started in has. The regeneration call that produces the new
state's own reply must ask for that state's own output fields too, the same
way a turn started fresh in that state would (see
TrackingProcessor.build_regeneration_prompt).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
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
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="env.ready")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.", output=("summary",))
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        general_attachments={},
        autotracking_on_ai_message=True,
        env_keys=[
            EnvKey(name="ready", type="bool", ai_definition="Whether to advance."),
            EnvKey(name="summary", type="string", ai_definition="Summary of state b."),
        ],
    )


class RecordingSchemaAiService:
    def __init__(self, output_json: str) -> None:
        self._output_json = output_json
        self.calls: list[dict[str, str]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.calls.append(dict(schema))
        on_metadata("output", self._output_json)
        yield "report "


async def test_the_target_states_own_output_is_requested_and_applied_on_a_mid_turn_transition(db):
    automaton = _automaton()
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    ai_service = RecordingSchemaAiService('{"summary": "done"}')
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_context)
    env = PersistedEnv(db, project_context, session_id)
    env.update_action_set({"ready": True}, origin="system")
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)
    user_variables = UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)
    processor = TrackingProcessorAfterUserMessage(ai_service, scope_builder, env, db, user_variables)

    result = await processor.process("hello")

    assert processor.out.state.key == "b"
    assert ai_service.calls and "output" in ai_service.calls[-1]
    assert env.action_set()["summary"] == "done"
    assert result["env_changed"]["summary"] == "done"
