"""What a user-initiated turn needs before it can run belongs to whoever
runs the turn — core, not the channel that posted the message.

A state that cannot take a turn at all still owes the person something:
the wrap-up its own state generates. That message is not in any turn's
reply (a turn response carries exactly one assistant message, its own)
and the turn that would have followed is refused, so the only frame that
can carry it is the terminal one. These tests pin that it does, as
a message of its own, published before the answer — and that the
preparation runs ahead of the refusal, since preparing after it would
mean never preparing at all.

The phone channel used to do this itself, around the turn, with its own
bootstrap and its own watermark. It was the only caller
TurnService.prepare_user_initiated_turn ever had.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton import Action, Automaton, State
from system import bus
from system.bus import INPUT_TEXT, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn_harness import PROJECT_ID, one_state_automaton, turn_service_for  # noqa: F401 — fixture

pytestmark = pytest.mark.regression

_WRAP_UP = "Your flight is on time."


class _FakeProvider:
    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        yield '{"text": "%s"}' % _WRAP_UP

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


def _chat_blocked_automaton() -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="A", final=True, chat_enabled=False, contextual_prompt="wrap up", actions=[]),
    }
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], general_attachments={},
        autotracking_on_ai_message=False, sources=[], project_id=PROJECT_ID,
    )


async def _frames_of(turn_service, db, text: str) -> list[Message]:
    bus._reset_for_tests()
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    frames: list[Message] = []
    finished = asyncio.Event()

    async def take(message: Message) -> None:
        frames.append(message)
        finished.set()

    async def take_terminal(message: Message) -> None:
        frames.append(message)
        finished.set()

    for message_type in ("output.text",):
        bus.subscribe(message_type, take)
    for message_type in ("ui.buttons", "output.error"):
        bus.subscribe(message_type, take_terminal)
    TurnInput(turn_service, db).register()

    await bus.publish(Message(
        type=INPUT_TEXT, body={"text": text}, username=WebSession().user,
        session_id=session["id"], channel="webchat", origin_id="connection-1", stream_id="turn-1",
    ))
    await asyncio.wait_for(finished.wait(), timeout=10)
    return frames


async def test_a_state_that_takes_no_messages_reports_its_wrap_up_on_the_failure(turn_service_for):
    """Fails when prepare_user_initiated_turn and accept_user_message are
    swapped: the refusal comes first and nothing is ever prepared."""
    turn_service = turn_service_for(_chat_blocked_automaton(), _FakeProvider())

    frames = await _frames_of(turn_service, turn_service_for.db, "hello?")

    assert frames[-1].type == "output.error"
    assert frames[-1].body["code"] == "state_not_chat"
    # What the state owed goes out as a message of its own, before the
    # refusal — the person is owed it whether or not the turn ran.
    assert [f.body["text"] for f in frames if f.type == "output.text"] == [_WRAP_UP]


async def test_an_ordinary_turn_prepares_nothing_and_leaves_reply_to_the_turn(turn_service_for):
    """The case webchat is. `reply[0]` is what chatStoreFactory.js
    reconciles a streamed bubble against, so nothing may precede the
    turn's own message in it."""
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(),
    )

    frames = await _frames_of(turn_service, turn_service_for.db, "hello")

    assert frames[-1].type == "output.text"
    # One message, the turn's own: nothing was owed first, so nothing
    # precedes it.
    assert [f.body["text"] for f in frames if f.type == "output.text"] == [_WRAP_UP]
