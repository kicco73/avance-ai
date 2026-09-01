"""action_prompt's dedicated entry point: an action_prompt is injected into
the model's prompt, never saved to the DB as a real user-authored
message.
"""
from __future__ import annotations

import inspect

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from conftest import NullBroadcaster, make_test_actuator_factory
from jobs import JobQueue
from metrics.metric_service import MetricService
from tracking.tracking_processor import TrackingProcessor
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

PROJECT_NAME = "proj"


def _automaton(*, chat_on_destination: bool = True) -> Automaton:
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target="b",
        action_prompt="Greet the user warmly.",
    )
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="You are in A.", actions=[action])
    state_b = State(
        key="b", ui_label="B", final=True, contextual_prompt="You are in B.", chat=chat_on_destination,
    )
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


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state(self, project_name: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

    def get_published_revision(self, project_name: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_name: str) -> bool:
        return False

    def get_project_availability(self, project_name: str):
        return (False, None)

    def apply_manual_action(self, action_name: str, session_id: int):
        automaton, state = self.get_active_automaton_and_state()
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        self._state_key = new_state.key
        return automaton.get_state_payload(new_state), action, state.key


def _chat_service(db, automaton: Automaton) -> tuple[ChatService, FakeAiService]:
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    actuator_factory = make_test_actuator_factory(db, job_queue)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    chat_service = ChatService(
        db=db, ai_service=ai_service, ai_test_service=ai_service, project_service=project_service,
        session_manager=ChatSessionManager(db), tracking_service=tracking_service, metric_service=metric_service,
        job_queue=job_queue, actuator_factory=actuator_factory,
    )
    return chat_service, ai_service


async def test_action_prompt_leaves_no_user_role_message_in_the_db(db):
    """An action_prompt is engine-generated text injected into the model's
    prompt, never a real user turn — none of the resulting assistant
    messages may ever be saved with role="user"."""
    chat_service, _ = _chat_service(db, _automaton())
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    messages = db.get_messages(session["id"])
    assert messages, "the action should have produced at least one message"
    assert all(m["role"] == "assistant" for m in messages)


async def test_action_prompt_fires_even_when_the_destination_state_disallows_chat(db):
    chat_service, _ = _chat_service(db, _automaton(chat_on_destination=False))
    session = chat_service.get_or_create_current_session(None)

    result = await chat_service.apply_manual_action("advance", session["id"])

    assert result["reply"], "action_prompt should still produce a reply even though state 'b' has chat=False"
    messages = db.get_messages(session["id"])
    assert messages
    assert all(m["role"] == "assistant" for m in messages)


async def test_apply_manual_action_reply_entries_are_flat_message_dicts(db):
    # apply_manual_action's "reply" list must be flat {id, role, content,
    # audio_text} dicts, matching what chatStore.js's handleAction expects.
    # State "b" is final and chat-enabled, exercising the two-entries case.
    chat_service, _ = _chat_service(db, _automaton(chat_on_destination=True))
    session = chat_service.get_or_create_current_session(None)

    result = await chat_service.apply_manual_action("advance", session["id"])

    assert len(result["reply"]) == 2
    for entry in result["reply"]:
        assert entry["role"] == "assistant"
        assert isinstance(entry["id"], int)
        assert isinstance(entry["content"], str) and entry["content"]


async def test_action_prompt_text_reaches_the_model_prompt_not_the_saved_message(db):
    chat_service, ai_service = _chat_service(db, _automaton())
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "Greet the user warmly." in system_prompt

    messages = db.get_messages(session["id"])
    assert all("Greet the user warmly." not in m["content"] for m in messages)


def test_process_turn_extra_prompt_still_defaults_to_none():
    """extra_prompt is optional, not required."""
    sig = inspect.signature(TrackingProcessor.process)
    assert sig.parameters["extra_prompt"].default is None
