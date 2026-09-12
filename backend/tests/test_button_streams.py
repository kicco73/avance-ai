"""A choice taken produces its message the same way an answer does.

It used to not: `apply_manual_action` was written as "apply the
transition and report the state", a leftover of the days when a button
was an HTTP POST whose reply came back as a payload. There was no
`on_metadata` anywhere on that path, so the message the new state wrote
never said it had started writing and never sent a piece of itself — it
landed on screen whole, with no bubble and no dots while it was being
written. To whoever is reading there is no difference between that
message and an answer, so there is none here either.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton import Action, Automaton, State
from system import bus
from system.bus import INPUT_BUTTON, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn_harness import FakeProjectService, PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

_PUBLISHED = (
    "output.text_stream", "output.text", "output.speech", "output.tool", "output.reaction",
    "state.changed", "ui.buttons", "output.error",
)
_REPLY = "Here we are."


class _FakeProvider:
    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        yield '{"text": "' + _REPLY + '"}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


class _MovingProjectService(FakeProjectService):
    """The harness' project service always answers with one fixed state.
    A button is a move, so this one follows it — the same three things
    the real one returns (see project/inspector.py)."""

    def __init__(self, automaton: Automaton) -> None:
        super().__init__(automaton, "a")

    def apply_manual_action(self, action_name: str, session_id: int):
        action = self._automaton.move(self._state_key, action_name)
        source_key, self._state_key = self._state_key, action.target
        state = self._automaton.states[self._state_key]
        return self._automaton.get_state_payload(state), action, source_key


def two_state_automaton() -> Automaton:
    """Somewhere to go and something to say on arrival: `history_cutoff`
    is what makes the new state speak first (see TurnService.
    _should_generate_opening_message)."""
    go = Action(name="go", ui_label="Go", ui_button="Go", target="b")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[go]),
        "b": State(key="b", ui_label="B", final=False, contextual_prompt="welcome", actions=[], history_cutoff=True),
    }
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], general_attachments={},
        autotracking_on_ai_message=False, sources=[], project_id=PROJECT_ID,
    )


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.finished = asyncio.Event()

    async def take(self, message: Message) -> None:
        self.messages.append(message)
        if message.type in ("output.text", "output.error"):
            self.finished.set()


async def test_a_button_says_it_is_writing_and_streams_what_it_writes(turn_service_for):
    db = turn_service_for.db
    automaton = two_state_automaton()
    turn_service = turn_service_for(automaton, _FakeProvider())
    turn_service._project_service = _MovingProjectService(automaton)

    bus._reset_for_tests()
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    recorder = _Recorder()
    for message_type in _PUBLISHED:
        bus.subscribe(message_type, recorder.take)
    TurnInput(turn_service, db).register()

    await bus.publish(Message(
        type=INPUT_BUTTON, body={"id": "go"}, username=WebSession().user,
        session_id=session["id"], channel="webchat", origin_id="connection-1",
    ))
    await asyncio.wait_for(recorder.finished.wait(), timeout=10)

    kinds = [m.type for m in recorder.messages]
    streamed = [m for m in recorder.messages if m.type == "output.text_stream"]
    assert streamed, kinds
    assert streamed[0].body["text"] == ""
    assert "".join(m.body["text"] for m in streamed) == _REPLY
    assert kinds.index("output.text_stream") < kinds.index("output.text")
