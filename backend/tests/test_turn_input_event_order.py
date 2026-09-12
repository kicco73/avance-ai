"""End to end, through the real listener (turn/input_listener.py) and a
real AiService driven by a fake provider: every frame a turn raises
reaches the Bus in the order it was raised, each with the turn's own
the session it belongs to, and the answer always comes last — after every chunk,
whether the turn made tool calls (a collected round replayed without ever
yielding the loop) or not.

What guarantees the order is no longer that nothing is scheduled. It used
to be: on_metadata is synchronous end to end (see tracking/
turn_callbacks.py's own OnMetadata) and the frames went straight onto a
websocket, so nothing could be overtaken. Publishing is not synchronous,
so the frames are queued as they are made — in order, from that same
synchronous callback — and one task drains the queue, awaiting each
publish before taking the next. These tests are what says the queue
actually does what the synchrony used to.
"""
from __future__ import annotations

import asyncio

import pytest

from ai.llm_provider import ToolCall, ToolCallsRequested
from system import bus
from system.bus import INPUT_TEXT, Message
from system.web_session import WebSession
from turn.input_listener import TurnInput
from turn.turn_service import TurnService
from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — turn_service_for is a fixture

pytestmark = pytest.mark.regression

_ANSWER_PIECES = ['{"text": "Your ', 'flight ', 'is on time."}']


class _FakeProvider:
    def __init__(self, *, tool_rounds: int) -> None:
        self._tool_rounds = tool_rounds
        self._round = 0

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self._round += 1
        if self._round <= self._tool_rounds:
            raise ToolCallsRequested(
                calls=[ToolCall(id=f"call_{self._round}", name="source_flights_select", arguments={"values": ["paris"]})],
                assistant_content=None,
            )
        for piece in _ANSWER_PIECES:
            yield piece

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096




def _is_terminal(kinds: list[str]) -> bool:
    """The answer is the `output.text` published after `ui.buttons` — an
    earlier one is a message the state owed before it could answer. An
    `output.error` replaces the answer and ends the exchange too."""
    return kinds[-1] == "output.error" or (kinds[-1] == "output.text" and "ui.buttons" in kinds)


class _Recorder:
    """Every frame the turn publishes, in arrival order, plus a way to
    know the turn is over — the listener runs it as its own task, so
    there is nothing to await from outside."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.finished = asyncio.Event()

    async def take(self, message: Message) -> None:
        self.messages.append(message)
        if _is_terminal([m.type for m in self.messages]):
            self.finished.set()


async def _streamed_events(turn_service: TurnService, db, text: str) -> list[tuple[str, dict]]:
    bus._reset_for_tests()
    # The listener looks the sender's role up rather than taking it off
    # the wire (see Session.for_sender), so the sender has to exist.
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    recorder = _Recorder()
    for message_type in (
        "output.text_stream", "output.text", "output.speech", "output.tool", "output.reaction",
        "state.changed", "ui.buttons", "output.error",
    ):
        bus.subscribe(message_type, recorder.take)
    TurnInput(turn_service, db).register()

    await bus.publish(Message(
        type=INPUT_TEXT, body={"text": text}, username=WebSession().user,
        session_id=session["id"], channel="webchat",
        origin_id="connection-1",
    ))
    await asyncio.wait_for(recorder.finished.wait(), timeout=10)

    assert {m.origin_id for m in recorder.messages} == {"connection-1"}
    return [(m.type, m.body if isinstance(m.body, dict) else {"body": m.body}) for m in recorder.messages]


def _kinds(events: list[tuple[str, dict]]) -> list[str]:
    """The empty chunk is its own kind here: it says the reply has started
    being written, and every other chunk carries some of it."""
    return [
        f"tool({data['phase']})" if event == "output.tool"
        else "writing" if (event == "output.text_stream" and data["text"] == "")
        else event
        for event, data in events
    ]


def _streamed_text(events: list[tuple[str, dict]]) -> str:
    return "".join(data["text"] for event, data in events if event == "output.text_stream")


async def test_with_declared_sources_every_chunk_of_the_replayed_final_round_precedes_done(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=True, autotracking_on_ai_message=True), _FakeProvider(tool_rounds=1),
    )

    events = await _streamed_events(turn_service, turn_service_for.db, "where's my flight?")

    kinds = _kinds(events)
    # The empty chunk always precedes generation (see tracking_processor.py's
    # own process()), before even the first tool call.
    assert kinds[0] == "writing"
    assert kinds[1:3] == ["tool(start)", "tool(result)"]
    chunk_kinds = kinds[3:-2]
    assert chunk_kinds and set(chunk_kinds) == {"output.text_stream"}
    assert kinds[-2:] == ["ui.buttons", "output.text"]
    assert _streamed_text(events) == "Your flight is on time."
    # The whole message is its own publication now, not a field of the
    # terminal frame (see turn/input_listener.py's own said()).
    assert [data["text"] for event, data in events if event == "output.text"] == ["Your flight is on time."]


async def test_without_sources_and_tracking_after_the_user_message_every_chunk_precedes_done(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(tool_rounds=0),
    )

    events = await _streamed_events(turn_service, turn_service_for.db, "hello")

    kinds = _kinds(events)
    assert kinds[0] == "writing"
    # Every piece first, then the whole message, then what can be done
    # next, then the terminal frame.
    assert kinds[-2:] == ["ui.buttons", "output.text"]
    assert set(kinds[1:-2]) == {"output.text_stream"}
    assert _streamed_text(events) == "Your flight is on time."


async def test_with_declared_sources_but_no_tool_call_the_answer_streams_then_done(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=True, autotracking_on_ai_message=True), _FakeProvider(tool_rounds=0),
    )

    events = await _streamed_events(turn_service, turn_service_for.db, "hello")

    kinds = _kinds(events)
    assert kinds[0] == "writing"
    # Every piece first, then the whole message, then what can be done
    # next, then the terminal frame.
    assert kinds[-2:] == ["ui.buttons", "output.text"]
    assert set(kinds[1:-2]) == {"output.text_stream"}
    assert _streamed_text(events) == "Your flight is on time."
