"""Whether a state's own `output` (see automaton.State.output) is asked
for before or after 'text' in the same call's schema (see
TrackingProcessor.build_turn_prompt) follows automaton.
autotracking_on_ai_message *and* whether `state` actually has a
triggerable action — never the project-wide setting alone. Nothing reads
output pre-reply for a state with none (TrackingEngine.
evaluate_triggered_action short-circuits on state.has_triggerable_actions),
so asking for it before the model has composed its own text only pressures
an output field meant to echo that text into a generic placeholder instead
(see State.has_triggerable_actions).
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
from tracking.tracking_processor_ai import TrackingProcessorAfterAiMessage

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


def _automaton(*, trigger: str | None) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger)
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action],
        output=("summary",),
    )
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        general_attachments={},
        autotracking_on_ai_message=False,
        env_keys=[EnvKey(name="summary", type="string", ai_definition="Echoes the reply just given.")],
    )


class RecordingSchemaAiService:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False):
        self.calls.append(dict(schema))
        on_metadata("output", '{"summary": "done"}')
        yield "reply "


async def _run(db, automaton: Automaton) -> RecordingSchemaAiService:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    ai_service = RecordingSchemaAiService()
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_context)
    env = PersistedEnv(db, project_context, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)
    user_variables = UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)
    processor = TrackingProcessorAfterAiMessage(ai_service, scope_builder, env, db, user_variables)
    await processor.process([])
    return ai_service


async def test_output_follows_text_for_a_state_with_no_triggerable_action(db):
    ai_service = await _run(db, _automaton(trigger=None))

    order = list(ai_service.calls[0])

    assert order.index("text") < order.index("output")


async def test_output_still_precedes_text_for_a_state_with_a_triggerable_action(db):
    ai_service = await _run(db, _automaton(trigger="env.summary == 'done'"))

    order = list(ai_service.calls[0])

    assert order.index("output") < order.index("text")
