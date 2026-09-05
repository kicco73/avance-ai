"""chat-service.input-token-budget-per-turn."""
from __future__ import annotations

from datetime import datetime

import pytest

from ai import AiService
from ai.llm_provider import ToolCall, ToolCallsRequested, ToolSpec
from automaton.automaton import Action, Automaton, State
from chat.errors import ChatServiceError
from conftest import make_test_actuator_factory
from metrics.metric_service import MetricService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_processor import UserVariables
from tracking.tracking_service import TrackingService
from tracking.user_facts import UserFacts
from tracking.tracking_processor_user import TrackingProcessorAfterUserMessage

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "budget_proj"


def _automaton(contextual_prompt: str = "hi") -> Automaton:
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt=contextual_prompt, actions=[])
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


class FakeProjectService:
    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states["a"]

    def get_active_project_id(self) -> str:
        return PROJECT_ID


class RecordingAiService:
    def __init__(self) -> None:
        self.called = False

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, **kwargs):
        self.called = True
        yield "should never run"  # pragma: no cover - the budget check must reject first


def _session_id(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )


async def test_a_turn_whose_system_prompt_alone_exceeds_the_budget_is_rejected_before_calling_the_provider(db):
    automaton = _automaton(contextual_prompt="x" * 200)
    session_id = _session_id(db)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, project_service)
    ai_service = RecordingAiService()
    service = TrackingService(
        db, project_service, metrics, make_test_actuator_factory(db), input_token_budget_per_turn=10,
    )

    with pytest.raises(ChatServiceError) as exc_info:
        await service._process(session_id, "hello", ai_service)

    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "input_budget_exceeded"
    assert ai_service.called is False
    warnings = db.get_system_warnings(USERNAME, PROJECT_ID)
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "input_budget_exceeded"
    assert "prompt" in warnings[0]["message"]


async def test_a_turn_within_budget_is_not_rejected(db):
    automaton = _automaton(contextual_prompt="hi")
    session_id = _session_id(db)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, project_service)
    ai_service_ok = RecordingAiServiceThatFinishes()
    service = TrackingService(
        db, project_service, metrics, make_test_actuator_factory(db), input_token_budget_per_turn=100000,
    )

    await service._process(session_id, "hello", ai_service_ok)

    assert ai_service_ok.called is True
    assert db.get_system_warnings(USERNAME, PROJECT_ID) == []


class RecordingAiServiceThatFinishes(RecordingAiService):
    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, **kwargs):
        self.called = True
        yield "hi"


class _FakeToolProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate_stream_with_schema(self, system_prompt, history, schema, on_metadata, tools=None, tool_round=None, required_tools=None):
        self.calls += 1

        async def _gen():
            raise ToolCallsRequested([ToolCall(id="1", name="lookup", arguments={})], "")
            yield  # pragma: no cover - unreachable, keeps this an async generator

        return _gen()


class _FakeToolSet:
    session_id = 1

    def specs(self):
        return [ToolSpec(name="lookup", description="d", parameters={})]

    def required_specs(self):
        return []

    def status_text(self, name: str) -> str:
        return "Looking something up..."

    def summary_text(self, name: str, arguments: dict, result: str) -> str:
        return "Done."

    async def call(self, name: str, arguments: dict) -> str:
        return "x" * 1000


async def test_ai_services_own_tool_loop_rejects_a_round_that_exceeds_the_budget():
    provider = _FakeToolProvider()
    ai_service = AiService(provider, input_token_budget_per_turn=50)

    with pytest.raises(ChatServiceError) as exc_info:
        async for _ in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "..."}, tool_set=_FakeToolSet(),
        ):
            pass

    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "input_budget_exceeded"
    assert provider.calls == 1


def _user_variables(automaton: Automaton, session_id: int) -> UserVariables:
    return UserVariables(automaton=automaton, state=automaton.states["a"], project_id=PROJECT_ID, session_id=session_id)


class MultiRoundAiService:

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, **kwargs):
        on_metadata("input_tokens", 100)
        on_metadata("output_tokens", 20)
        on_metadata("input_tokens", 150)
        on_metadata("output_tokens", 30)
        yield "hi"


async def test_message_tokens_is_the_sum_of_every_rounds_own_input_tokens(db):
    automaton = _automaton(contextual_prompt="hi")
    session_id = _session_id(db)
    project_service = FixedProjectContext(project_id=PROJECT_ID)
    metrics = MetricService(db, project_service)
    env = PersistedEnv(db, project_service, session_id)
    scope_builder = EvaluationScopeBuilder(env, metrics, SessionFacts(db, project_service), UserFacts(db), db)

    processor = TrackingProcessorAfterUserMessage(
        MultiRoundAiService(), scope_builder, env, db, _user_variables(automaton, session_id),
    )
    result = await processor.process("hello")

    user_message = db.get_message(result["user_message_id"])
    assert user_message["tokens"] == 250
