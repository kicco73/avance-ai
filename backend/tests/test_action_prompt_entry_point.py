"""action_prompt's own dedicated entry point (see chat_service.py's
_generate_action_prompt_message, tracking/tracking_processor.py's
extra_prompt/_save_user_message) — an action_prompt is injected into the
model's own prompt, never saved to the DB as a real user-authored
message. Regression coverage for the bug this replaced: action_prompt
used to be passed as `text`, permanently polluting the conversation
history with a fake role="user" message that was never cleaned up.
"""
from __future__ import annotations

import inspect

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
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

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

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
    metric_service = MetricService(
        db, get_username=lambda: "user", get_active_project_name=lambda: PROJECT_NAME,
    )
    tracking_service = TrackingService(db, ai_service, project_service, metric_service)
    chat_service = ChatService(
        db=db, ai_service=ai_service, project_service=project_service,
        session_manager=ChatSessionManager(db), tracking_service=tracking_service, metric_service=metric_service,
    )
    return chat_service, ai_service


async def test_action_prompt_leaves_no_user_role_message_in_the_db(db):
    """The whole point of the fix: an action_prompt is engine-generated
    text injected into the model's own prompt (see extra_prompt), never a
    real user turn — regardless of how many assistant messages the
    transition itself produces (the action_prompt reply, plus a separate
    opening message if the destination state needs one too, see
    ChatService._messages_for_transition/PROJECT_SPECS.md §6.3), none of
    them may ever be saved with role="user"."""
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
    # Regression test: apply_manual_action's own "reply" list used to be
    # a list of nested turn-response dicts (each carrying only ids —
    # process_turn's own return shape, see _build_turn_response — never
    # the reply's own text). chatStore.js's handleAction has always
    # destructured {id, content, audio_text} straight off each entry,
    # which silently produced content: undefined/audioText: undefined/
    # messageId: undefined bubbles for every action-triggered follow-up
    # message. _messages_for_transition now resolves each turn's own
    # assistant_message_id through db.get_message, returning the flat
    # {id, role, content, audio_text, ...} shape the frontend already
    # expects. State "b" here is both final and chat-enabled, so this
    # exercises the two-entries case: the action_prompt's own reply,
    # plus a separate opening message for landing in "b".
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
    """_generate_opening_message_body/process_turn's own plain call
    (no extra_prompt) must keep behaving exactly as before this change —
    confirms the new parameter is additive, not a required one."""
    sig = inspect.signature(TrackingProcessor.process)
    assert sig.parameters["extra_prompt"].default is None
