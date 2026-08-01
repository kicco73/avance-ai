"""Integration-ish tests for how a real chat turn decides
Message.is_evaluation_point and links a Signals row to the message that
caused it (see ChatService._process_turn_locked/_run_auto_tracking,
db.link_signal_to_message) — and for the expected_state/expected_values
annotation writes that only an evaluation-point message allows (see
ChatService.set_message_expected_state/set_message_expected_signals).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from chat.chat_service import ChatService, ChatServiceError
from chat.session_manager import ChatSessionManager

PROJECT_NAME = "proj"


def _automaton(*, autotracking_on_user_message=False, autotracking_on_ai_message=False) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="foo >= 0")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": state_a,
        "b": state_b,
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[Signal(name="foo", ui_label="Foo", definition="foo definition")],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=autotracking_on_user_message,
        autotracking_on_ai_message=autotracking_on_ai_message,
    )


class FakeProjectService:
    """Stands in for project.project_service.ProjectService — just enough
    of its interface for ChatService to run a real turn against a fixed,
    hand-built automaton (see _automaton above), no file/YAML involved."""

    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME


class TaggedAiService:
    """Like conftest.py's FakeAiService, but the reply embeds a real
    [avance]{"signals": {...}}[/avance] tag — so AutoTracker.run() gets a
    genuinely non-empty signal_values dict straight from the metadata
    (see MetadataHandler.signal_values) and never falls back to actually
    calling the AI a second time to compute signals from scratch."""

    def __init__(self, signals: dict) -> None:
        self._reply = f'Hi![avance]{{"signals": {signals!r}}}[/avance]'.replace("'", '"')

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    async def generate(self, system_prompt, history, on_retry=None) -> str:
        return self._reply

    async def generate_stream(self, system_prompt, history, on_retry=None):
        yield self._reply


@pytest.fixture
def chat_service_for(db):
    def make(automaton: Automaton, *, signal_values: dict = {"foo": 1}) -> ChatService:
        service = ChatService(
            ai_service=TaggedAiService(signal_values),
            project_service=FakeProjectService(automaton),
            db=db,
            session_manager=ChatSessionManager(db),
        )
        return service

    return make


async def _bootstrap_session(chat_service: ChatService) -> int:
    session = chat_service.get_or_create_current_session(None)
    return session["id"]


async def test_ai_message_evaluation_marks_the_assistant_message_as_an_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn("hello", session_id)

    assistant_message = next(m for m in result["reply"] if m.get("id") is not None)
    stored = db.get_message(assistant_message["id"])
    assert stored["is_evaluation_point"] is True
    assert stored["role"] == "assistant"

    linked = db.get_signal_row_by_message(assistant_message["id"])
    assert linked is not None
    assert linked["new_state"] == "b"  # the trigger fired: foo >= 0


async def test_no_autotracking_means_no_evaluation_point_at_all(db, chat_service_for):
    chat_service = chat_service_for(_automaton())  # both autotracking flags default False
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn("hello", session_id)

    assistant_message = next(m for m in result["reply"] if m.get("id") is not None)
    assert db.get_message(assistant_message["id"])["is_evaluation_point"] is False
    assert db.get_signal_row_by_message(assistant_message["id"]) is None


async def test_set_message_expected_state_on_a_real_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    updated = chat_service.set_message_expected_state(message_id, "a")
    assert updated["expected_state"] == "a"

    cleared = chat_service.set_message_expected_state(message_id, None)
    assert cleared["expected_state"] is None


async def test_set_message_expected_state_rejects_an_unknown_state(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(ChatServiceError):
        chat_service.set_message_expected_state(message_id, "not-a-real-state")


async def test_set_message_expected_state_rejects_a_non_evaluation_point_message(db, chat_service_for):
    chat_service = chat_service_for(_automaton())  # no autotracking, so nothing is annotatable
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(ChatServiceError):
        chat_service.set_message_expected_state(message_id, "a")


async def test_set_message_expected_signals_on_a_real_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    updated = chat_service.set_message_expected_signals(message_id, {"foo": 75})
    assert updated["expected_values"] == '{"foo": 75}'
    # The actually-observed values must stay untouched.
    assert updated["values"] is not None

    cleared = chat_service.set_message_expected_signals(message_id, None)
    assert cleared["expected_values"] is None


async def test_set_message_expected_signals_rejects_an_unknown_signal_name(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(ChatServiceError):
        chat_service.set_message_expected_signals(message_id, {"not-a-real-signal": 50})


async def test_set_message_expected_signals_rejects_an_out_of_range_value(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(ChatServiceError):
        chat_service.set_message_expected_signals(message_id, {"foo": 150})
