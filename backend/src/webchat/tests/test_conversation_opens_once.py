"""A conversation opens once, however many people ask it to.

Two tabs enter the same empty conversation in the same instant, are each
announced the same empty transcript, and each is told it has just been
opened (see turn/input_listener.py). Nobody works out whether it has
spoken yet — the chat that is already opening it is writing the very
message the second event asks for, so the second event finds it there and
leaves it to write.
"""
from __future__ import annotations

import asyncio

import pytest

from system import bus
from system.bus import OUTPUT_TEXT, SESSION_OPENED, Message
from system.web_session import WebSession
from webchat.conversation_opener import ConversationOpener
from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — a pytest fixture, used by name

pytestmark = pytest.mark.regression


class _SlowProvider:
    """Slow enough that the second event arrives while the first is still
    writing — which is the whole of the race, and is what a real model
    does by taking seconds to answer. It counts the moment it is asked,
    before it answers: a second opening is visible here long before its
    message is."""

    def __init__(self) -> None:
        self.answers = 0

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.answers += 1
        await asyncio.sleep(0.05)
        yield '{"text": "Welcome."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


class _Greeted:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.arrived = asyncio.Event()

    async def take(self, message: Message) -> None:
        self.texts.append(message.body["text"])
        self.arrived.set()


def _opened(session_id: int, origin_id: str) -> Message:
    return Message(
        type=SESSION_OPENED, body={}, username=WebSession().user, session_id=session_id,
        channel="webchat", origin_id=origin_id,
    )


async def test_two_tabs_opening_at_once_are_greeted_once(turn_service_for):
    bus._reset_for_tests()
    db = turn_service_for.db
    provider = _SlowProvider()
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.get_current_session_if_any_or_create_new(None)
    greeted = _Greeted()
    bus.subscribe(OUTPUT_TEXT, greeted.take)
    ConversationOpener(turn_service, db).register()

    await asyncio.gather(
        bus.publish(_opened(session["id"], "connection-1")),
        bus.publish(_opened(session["id"], "connection-2")),
    )
    await asyncio.wait_for(greeted.arrived.wait(), timeout=10)

    assert provider.answers == 1
    assert greeted.texts == ["Welcome."]
    said = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert [m["content"] for m in said] == ["Welcome."]
