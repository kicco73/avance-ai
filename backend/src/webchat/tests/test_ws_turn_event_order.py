"""End to end, through a real WsChatTurn (see chat/ws_turn.py) and a real
AiService driven by a fake provider: every frame a turn raises reaches the
connection in the order it was raised, each with the turn's own turn_id,
and "turn.ended" always comes last — after every chunk, whether the turn made
tool calls (a collected round replayed without ever yielding the loop) or
not. on_metadata is synchronous end to end (see tracking/turn_callbacks.py's
own OnMetadata): nothing is scheduled, so nothing can be overtaken.
"""
from __future__ import annotations

import pytest

from ai.llm_provider import ToolCall, ToolCallsRequested
from turn.turn_service import TurnService
from webchat.ws_turn import WsChatTurn
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




class _RecordingConnection:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def send(self, payload: dict) -> None:
        self.frames.append(payload)


async def _streamed_events(turn_service: TurnService, text: str) -> list[tuple[str, dict]]:
    session = await turn_service.get_current_session_if_any_or_create_new(None)
    connection = _RecordingConnection()
    turn = WsChatTurn(turn_service, connection.send, "turn-1", session["id"], text)
    assert turn.accept()
    await turn.run()
    assert {frame["stream_id"] for frame in connection.frames} == {"turn-1"}
    return [(frame["type"], frame) for frame in connection.frames]


def _kinds(events: list[tuple[str, dict]]) -> list[str]:
    return [
        f"tool({data['phase']})" if event == "turn.tool" else event
        for event, data in events
    ]


def _streamed_text(events: list[tuple[str, dict]]) -> str:
    return "".join(data["body"] for event, data in events if event == "output.text")


async def test_with_declared_sources_every_chunk_of_the_replayed_final_round_precedes_done(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=True, autotracking_on_ai_message=True), _FakeProvider(tool_rounds=1),
    )

    events = await _streamed_events(turn_service, "where's my flight?")

    kinds = _kinds(events)
    # "turn.started" always precedes generation (see tracking_processor.py's
    # own process()), before even the first tool call.
    assert kinds[0] == "turn.started"
    assert kinds[1:3] == ["tool(start)", "tool(result)"]
    assert kinds[-1] == "turn.ended"
    chunk_kinds = kinds[3:-1]
    assert chunk_kinds and set(chunk_kinds) == {"output.text"}
    assert _streamed_text(events) == "Your flight is on time."
    assert events[-1][1]["reply"][0]["content"] == "Your flight is on time."


async def test_without_sources_and_tracking_after_the_user_message_every_chunk_precedes_done(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(tool_rounds=0),
    )

    events = await _streamed_events(turn_service, "hello")

    kinds = _kinds(events)
    assert kinds[-1] == "turn.ended"
    assert kinds[0] == "turn.started"
    assert kinds[1:-1] and set(kinds[1:-1]) == {"output.text"}
    assert _streamed_text(events) == "Your flight is on time."


async def test_with_declared_sources_but_no_tool_call_the_answer_streams_then_done(turn_service_for):
    turn_service = turn_service_for(
        one_state_automaton(with_sources=True, autotracking_on_ai_message=True), _FakeProvider(tool_rounds=0),
    )

    events = await _streamed_events(turn_service, "hello")

    kinds = _kinds(events)
    assert kinds[-1] == "turn.ended"
    assert kinds[0] == "turn.started"
    assert kinds[1:-1] and set(kinds[1:-1]) == {"output.text"}
    assert _streamed_text(events) == "Your flight is on time."
