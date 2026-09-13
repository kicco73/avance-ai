"""Entering a chat and then talking in it, through the real listener.

`session.enter` is the only way into a conversation: it names a project,
the server resolves or creates the session, and answers with what the
conversation is (`session.info`), what was said (`session.messages`),
what it offers (`state.buttons`) and — if the state has one — its
opening message.

Then the person types without waiting for anything, and that request
lands on the same session. Both are owed an answer, the second one
especially: a chat where the first thing you type is never answered is a
chat that does not work.
"""
from __future__ import annotations

import asyncio

import pytest

from system import bus
from system.bus import INPUT_TEXT, SESSION_ENTER, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn_harness import PROJECT_ID, one_state_automaton, turn_service_for  # noqa: F401 — turn_service_for is a fixture

pytestmark = pytest.mark.regression

_PUBLISHED = (
    "output.text_stream", "output.text", "output.speech", "output.tool", "output.reaction",
    "state.changed", "state.buttons", "output.error",
    "session.info", "session.messages", "session.blocked", "session.ended",
)


class _FakeProvider:
    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        yield '{"text": "hello"}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


class _Recorder:
    def __init__(self, answers: int) -> None:
        self.messages: list[Message] = []
        self._answers = answers
        self.finished = asyncio.Event()

    async def take(self, message: Message) -> None:
        self.messages.append(message)
        said = [m for m in self.messages if m.type in ("output.text", "output.error")]
        if len(said) >= self._answers:
            self.finished.set()

    def kinds(self) -> list[str]:
        return [m.type for m in self.messages]

    def texts(self) -> list[str]:
        return [m.body["text"] for m in self.messages if m.type == "output.text"]


async def _drive(turn_service, db, requests: list[Message], answers: int) -> tuple[_Recorder, TurnInput]:
    """Publishes each request the way a channel does — without waiting for
    the one before to be answered — and gives back what came out."""
    bus._reset_for_tests()
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    recorder = _Recorder(answers)
    for message_type in _PUBLISHED:
        bus.subscribe(message_type, recorder.take)
    listener = TurnInput(turn_service, db)
    listener.register()
    for request in requests:
        await bus.publish(request)
    await asyncio.wait_for(recorder.finished.wait(), timeout=10)
    return recorder, listener


def _asked(kind: str, session_id: int, body: dict) -> Message:
    return Message(
        type=kind, body=body, username=WebSession().user, session_id=session_id,
        channel="webchat", origin_id="connection-1",
    )


def _entering(project_id: str) -> Message:
    return Message(
        type=SESSION_ENTER, body={"type": "live"}, username=WebSession().user,
        project_id=project_id, channel="webchat", origin_id="connection-1",
    )


async def test_entering_says_what_the_conversation_is_before_anything_it_says(turn_service_for):
    """The order matters and used to be the other way round: the pieces
    of the opening message came out before what framed them, so a chat
    could be reading a reply before it knew where it stood."""
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(),
    )

    recorder, _ = await _drive(turn_service, turn_service_for.db, [_entering(PROJECT_ID)], answers=1)

    kinds = recorder.kinds()
    assert kinds[:3] == ["session.info", "session.messages", "state.buttons"]
    assert recorder.texts() == ["hello"]
    assert kinds.index("state.buttons") < kinds.index("output.text")


async def test_entering_names_the_project_and_the_conversation_comes_back(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(),
    )

    recorder, _ = await _drive(turn_service, turn_service_for.db, [_entering(PROJECT_ID)], answers=1)

    info = next(m for m in recorder.messages if m.type == "session.info")
    assert info.session_id is not None
    assert info.body["project_id"] == PROJECT_ID
    assert info.body["state"]["key"] == "a"


async def test_typing_the_moment_the_chat_opens_is_still_answered(turn_service_for):
    """What a browser really does: it enters and the person starts typing
    without waiting. The opener may well be dropped — a conversation is
    only opened for someone who has said nothing — but what was typed is
    answered, and nothing stays in the queue."""
    db = turn_service_for.db
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(),
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    recorder, listener = await _drive(turn_service, db, [
        _entering(PROJECT_ID),
        _asked(INPUT_TEXT, session["id"], {"text": "hi"}),
    ], answers=1)

    assert recorder.texts() == ["hello"]
    assert listener._requests == {}
    said = db.get_messages(session["id"])
    assert [(m["role"], m["content"]) for m in said] == [("user", "hi"), ("assistant", "hello")]
