"""A state's own `output` (see automaton.State.output) is copied
automatically onto the real env keys it names once the turn completes —
no action.env needed — and the fresh value is already visible to this
same turn's own trigger evaluation (see tracking.evaluation_scope).
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


def _automaton(*, trigger: str | None = None) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger)
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action],
        output=("status", "confidence"),
    )
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=True,
        env_keys=[
            EnvKey(name="status", ai_definition="Where things stand."),
            EnvKey(name="confidence", ai_definition="0-100."),
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
        yield "reply "


def _processor(db, automaton: Automaton, output_json: str) -> tuple[TrackingProcessorAfterAiMessage, RecordingSchemaAiService, PersistedEnv]:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )
    ai_service = RecordingSchemaAiService(output_json)
    project_context = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_context)
    env = PersistedEnv(db, project_context, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_context), UserFacts(db), db)
    user_variables = UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)
    processor = TrackingProcessorAfterAiMessage(ai_service, scope_builder, env, db, user_variables)
    return processor, ai_service, env


async def test_output_values_are_copied_onto_the_real_env_keys_they_name(db):
    automaton = _automaton()
    processor, _, env = _processor(db, automaton, '{"status": "done", "confidence": 90}')

    await processor.process("hello")

    assert env.action_set() == {"status": "done", "confidence": 90}


async def test_a_key_not_in_the_states_own_output_is_never_copied(db):
    automaton = _automaton()
    # 'extra' isn't declared in state "a"'s own `output` — a hallucinated
    # or stale field must never leak into the automaton's real env.
    processor, _, env = _processor(db, automaton, '{"status": "done", "extra": "nope"}')

    await processor.process("hello")

    assert env.action_set() == {"status": "done"}


async def test_a_trigger_this_same_turn_already_sees_the_fresh_output_value(db):
    automaton = _automaton(trigger="env.status == 'done'")
    processor, _, env = _processor(db, automaton, '{"status": "done", "confidence": 90}')

    await processor.process("hello")

    assert processor.out.state.key == "b"
    assert env.action_set()["status"] == "done"
