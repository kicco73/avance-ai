"""End to end, through the real listener (turn/input_listener.py): the
signal values an exchange ran on reach the Bus as `state.signals`, so a
reader learns them as they are measured instead of waiting for a refetch
it has no reason to make (see docs/BUS.md).
"""
from __future__ import annotations

import asyncio

import pytest

from ai.llm_provider import LLMProvider
from automaton.automaton import Action, Automaton, State
from automaton.model import Signal
from system import bus
from system.bus import INPUT_BUTTON, INPUT_TEXT, STATE_SIGNALS, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn_harness import PROJECT_ID, TURN_FRAMES, _is_terminal, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

MOOD = Signal(name="mood", ui_label="Mood", definition="how it is going")


class _FakeProvider(LLMProvider):
    """Answers with this turn's signal values alongside the text, the way
    an auto-tracking turn's own structured output carries them."""

    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        yield '{"signals": {"mood": 0.5}, "text": "noted."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


def _automaton(target: str) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target=target)
    state_a = State(
        input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
    )
    state_b = State(input_processor="ai", key="b", ui_label="B", final=True, contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={
            "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
            "a": state_a, "b": state_b,
        },
        general_prompt="", signals=[MOOD], general_attachments={},
        autotracking_on_ai_message=True, project_id=PROJECT_ID,
    )


async def _exchange(turn_service, db, session_id: int, sent: Message) -> list[dict]:
    """One exchange, with the `state.signals` bodies it published."""
    collected: list[Message] = []
    signalled: list[dict] = []
    finished = asyncio.Event()

    async def take(message: Message) -> None:
        if message.session_id != session_id:
            return
        collected.append(message)
        if _is_terminal([m.type for m in collected]):
            finished.set()

    async def take_signals(message: Message) -> None:
        if message.session_id != session_id:
            return
        signalled.append(message.body)

    for message_type in TURN_FRAMES:
        bus.subscribe(message_type, take)
    bus.subscribe(STATE_SIGNALS, take_signals)
    if not any(getattr(listener, "__self__", None).__class__ is TurnInput for listener in bus.handlers_for(INPUT_TEXT)):
        TurnInput(turn_service, db).register()
    try:
        await bus.publish(sent)
        await asyncio.wait_for(finished.wait(), timeout=10)
    finally:
        for message_type in TURN_FRAMES:
            bus.unsubscribe(message_type, take)
        bus.unsubscribe(STATE_SIGNALS, take_signals)
    return signalled


def _sent(type: str, body: dict, session_id: int) -> Message:
    return Message(
        type=type, body=body, username=WebSession().user, session_id=session_id,
        channel="webchat", origin_id="connection-1",
    )


async def _started(turn_service, db):
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    return session["id"]


async def test_a_turn_says_the_signal_values_it_measured(turn_service_for):
    turn_service = turn_service_for(_automaton(target="a"), _FakeProvider())
    db = turn_service_for.db
    session_id = await _started(turn_service, db)

    signalled = await _exchange(turn_service, db, session_id, _sent(INPUT_TEXT, {"text": "hello"}, session_id))

    assert signalled == [{"values": {"mood": 0.5}}]


async def test_a_button_says_the_values_it_carried_forward_rather_than_going_silent(turn_service_for):
    """Pressing a button measures nothing of its own. It runs on what the
    last turn measured, and says so, so a reader watching the signals is
    not left with a gap it cannot tell apart from a change."""
    turn_service = turn_service_for(_automaton(target="b"), _FakeProvider())
    db = turn_service_for.db
    session_id = await _started(turn_service, db)
    await _exchange(turn_service, db, session_id, _sent(INPUT_TEXT, {"text": "hello"}, session_id))

    signalled = await _exchange(turn_service, db, session_id, _sent(INPUT_BUTTON, {"id": "advance"}, session_id))

    assert signalled == [{"values": {"mood": 0.5}}]
