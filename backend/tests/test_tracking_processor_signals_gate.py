"""TrackingProcessor.build_turn_protocol's own evaluate_signals gate must
also account for whether anything is actually triggerable from the state
the reply is being generated for (see automaton.triggerable_signal_names)
— asking the model to calculate signal values nothing in that state could
ever act on is pure waste: no definition in the prompt, no 'signals'
field in the schema. The gate only ever switches off that *request*: the
trigger evaluation itself still runs every turn with a real user message,
against the empty signals set (see test_auto_tracker_metrics.py for a
metric-/env-only trigger firing with no signal requested at all) —
*except* at the opening turn (the automaton's own AI-generated first
message, no real user message behind it yet), which skips that
evaluation outright rather than let an env./metric./source.*-only
trigger fire off the opener alone.
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


def _automaton(*, triggerable_from_a: bool) -> Automaton:
    """A signal is declared project-wide either way — `triggerable_from_a`
    only controls whether state "a"'s own action actually references it
    in a trigger, which is exactly what triggerable_signal_names checks."""
    mood = Signal(name="mood", ui_label="Mood", definition="0-100 mood score.")
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="b",
        trigger="signal.mood >= 50" if triggerable_from_a else None,
    )
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
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        self.calls.append(dict(schema))
        yield "reply "


def _processor(db, automaton: Automaton) -> tuple[TrackingProcessorAfterUserMessage, RecordingSchemaAiService]:
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
    processor = TrackingProcessorAfterUserMessage(ai_service, scope_builder, env, db, user_variables)
    return processor, ai_service


async def test_a_state_with_nothing_triggerable_never_requests_signals(db):
    # has_to_evaluate_signals_before_ai_reply is True here (autotracking_on_ai_message=False)
    # and this is a real user message (not AI-started) — under the old gate,
    # evaluate_signals would have been True regardless of whether anything
    # in state "a" could ever act on a signal value at all.
    automaton = _automaton(triggerable_from_a=False)
    processor, ai_service = _processor(db, automaton)

    await processor.process("hello")

    assert len(ai_service.calls) == 1
    assert "signals" not in ai_service.calls[0]


async def test_a_state_with_something_triggerable_still_requests_signals(db):
    automaton = _automaton(triggerable_from_a=True)
    processor, ai_service = _processor(db, automaton)

    await processor.process("hello")

    assert len(ai_service.calls) == 1
    assert "signals" in ai_service.calls[0]


def _env_only_trigger_automaton() -> Automaton:
    """`advance`'s own trigger references only env.ready — no signal at
    all, so triggerable_signal_names("a") is empty and _evaluate_signals_
    for is False regardless of turn type; that's exactly the case the
    opening-turn gate has to distinguish from "asks for nothing, but
    still evaluates" (the real-user-message case above)."""
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="env.ready == True")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


async def test_an_env_only_trigger_never_fires_at_the_opening_turn(db):
    automaton = _env_only_trigger_automaton()
    processor, _ = _processor(db, automaton)
    processor.env.update_action_set({"ready": True})

    await processor.process(None)  # the automaton's own AI-generated opener, no real user message

    assert processor.out.state.key == "a"  # never transitioned to "b"


async def test_the_same_env_only_trigger_fires_on_the_first_real_user_message(db):
    automaton = _env_only_trigger_automaton()
    processor, _ = _processor(db, automaton)
    processor.env.update_action_set({"ready": True})

    await processor.process("hello")

    assert processor.out.state.key == "b"
