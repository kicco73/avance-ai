"""Opening a chat and then talking in it, through the real listener.

This is what a browser does the moment somebody opens the page: it says
`session.new`, and the person starts typing without waiting for anything.
Both requests land on the same session, one after the other, and both are
owed an answer — the second one especially, since a chat where the first
thing you type is never answered is a chat that does not work.
"""
from __future__ import annotations

import asyncio

import pytest

from system import bus
from system.bus import INPUT_TEXT, SESSION_NEW, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — turn_service_for is a fixture

pytestmark = pytest.mark.regression

_PUBLISHED = (
    "output.text_stream", "output.text", "output.speech", "output.tool", "output.reaction",
    "state.changed", "ui.buttons", "output.error",
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


async def test_typing_the_moment_the_chat_opens_is_still_answered(turn_service_for):
    """What a browser really does: it says `session.new` and the person
    starts typing without waiting. The two requests queue up together —
    the opener may well be dropped, since somebody has since spoken and
    a conversation is only opened for someone who has said nothing — but
    what was typed is answered, and nothing stays in the queue."""
    db = turn_service_for.db
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(),
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    recorder, listener = await _drive(turn_service, db, [
        _asked(SESSION_NEW, session["id"], {}),
        _asked(INPUT_TEXT, session["id"], {"text": "hi"}),
    ], answers=1)

    assert recorder.texts() == ["hello"]
    assert listener._requests == {}
    said = db.get_messages(session["id"])
    assert [(m["role"], m["content"]) for m in said] == [("user", "hi"), ("assistant", "hello")]


async def test_a_chat_opened_and_never_typed_in_still_gets_its_first_message(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(),
    )
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    recorder, _ = await _drive(turn_service, turn_service_for.db, [
        _asked(SESSION_NEW, session["id"], {}),
    ], answers=1)

    assert recorder.texts() == ["hello"]
    assert recorder.kinds().index("ui.buttons") < recorder.kinds().index("output.text")
