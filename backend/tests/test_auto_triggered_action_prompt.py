"""Regression coverage for a bug found while auditing action_prompt's own
entry points (see test_action_prompt_entry_point.py, which only covers the
*manual* one): an action's own action_prompt used to reach the model's
prompt only when that action fired via a button click (ChatService.
apply_manual_action -> _generate_action_prompt_message, which runs it as
its own dedicated extra_prompt turn) — an *auto*-triggered action (its own
`trigger:` firing during a normal chat turn) silently dropped it, even
though the field means the same thing either way: "acknowledge that this
transition just happened." Fixed in tracking_processor_user.py's own
on_receiving_metadata_that_may_trigger_status_change, which now sets
self.extra_prompt itself, the moment the fired action is known, so the
existing regeneration-on-wrong-guess pass (see tracking_processor.py's
own __build_turn_prompt_parts) already folds it in like any other.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.asyncio

PROJECT_NAME = "proj"


def _automaton(action_prompt: str | None) -> Automaton:
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="b",
        trigger="signal.foo >= 0", action_prompt=action_prompt,
    )
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="You are in B.")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[Signal(name="foo", ui_label="Foo", definition="foo definition")],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME


class RecordingSchemaAiService:
    """Same v2/schema wire shape as test_chat_service_evaluation_points.
    py's own FakeSchemaAiService, plus recording each call's own
    system_prompt — that file never needed to inspect prompt content,
    only call count/linkage, so it never bothered."""

    def __init__(self, metadata_per_call: list[dict]) -> None:
        self._metadata_per_call = metadata_per_call
        self.call_count = 0
        self.system_prompts: list[str] = []

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        self.system_prompts.append(system_prompt)
        index = min(self.call_count, len(self._metadata_per_call) - 1)
        metadata = self._metadata_per_call[index]
        self.call_count += 1
        for key, value in metadata.items():
            on_metadata(key, value)
        yield "Hi!"


def _chat_service(db, automaton: Automaton, ai_service: RecordingSchemaAiService) -> ChatService:
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, get_username=lambda: "user", get_active_project_name=lambda: PROJECT_NAME)
    tracking_service = TrackingService(db, ai_service, project_service, metric_service)
    return ChatService(
        ai_service=ai_service, project_service=project_service, db=db,
        session_manager=ChatSessionManager(db), tracking_service=tracking_service, metric_service=metric_service,
    )


async def test_an_auto_triggered_actions_own_action_prompt_reaches_the_regenerated_reply(db):
    ai_service = RecordingSchemaAiService([{"signals": '{"foo": 1}'}, {"signals": '{"foo": 1}'}])
    chat_service = _chat_service(db, _automaton(action_prompt="Congratulate the user warmly."), ai_service)
    session = chat_service.get_or_create_current_session(None)
    ai_service.system_prompts.clear()  # bootstrap's own init-action opening message doesn't count
    ai_service.call_count = 0

    result = await chat_service.process_turn(session["id"], "hello")

    assert result["new_state"] == "b"
    assert len(ai_service.system_prompts) == 2
    assert "Congratulate the user warmly." not in ai_service.system_prompts[0]
    assert "Congratulate the user warmly." in ai_service.system_prompts[1]


async def test_no_action_prompt_leaves_the_regenerated_reply_unaffected(db):
    ai_service = RecordingSchemaAiService([{"signals": '{"foo": 1}'}, {"signals": '{"foo": 1}'}])
    chat_service = _chat_service(db, _automaton(action_prompt=None), ai_service)
    session = chat_service.get_or_create_current_session(None)
    ai_service.system_prompts.clear()
    ai_service.call_count = 0

    result = await chat_service.process_turn(session["id"], "hello")

    assert result["new_state"] == "b"
    assert "You are in B." in ai_service.system_prompts[1]
    assert "Congratulate" not in ai_service.system_prompts[1]
