"""End to end, through the real SseChatTurn.response() stream and a real
AiService driven by a fake provider: every event a turn raises reaches the
SSE stream in the order it was raised, and "done" always comes last —
after every chunk, whether the turn made tool calls (a collected round
replayed without ever yielding the loop) or not. on_metadata is
synchronous end to end (see tracking/turn_callbacks.py's own OnMetadata):
nothing is scheduled, so nothing can be overtaken.
"""
from __future__ import annotations

import json

import pytest

from ai.ai_service import AiService
from ai.llm_provider import ToolCall, ToolCallsRequested
from automaton.automaton import Action, Automaton, Source, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from chat.sse_turn import SseChatTurn
from conftest import make_test_actuator_factory, make_test_job_service
from db.db import Db
from metrics.metric_service import MetricService
from test_chat_tool_set_integration import FakeProjectService, PROJECT_ID
from tracking.tracking_service import TrackingService

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


def _automaton(*, with_sources: bool, autotracking_on_ai_message: bool) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
        ai_may_read_sources=("flights",) if with_sources else (),
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a}
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], attachments={},
        general_attachments={}, autotracking_on_ai_message=autotracking_on_ai_message,
        sources=[Source(name="flights", url="avance:flights.csv", ui_label="Flights", ai_definition="One row per flight.")]
        if with_sources else [],
        project_id=PROJECT_ID,
    )


@pytest.fixture
def chat_service_for(tmp_path):
    db = Db(f"sqlite:///{tmp_path / 'sse_order.db'}")
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"flights.csv": b"city,country\nParis,France\n"}, {"flights.csv": "text/csv"})
    db.publish_project(PROJECT_ID)

    def make(automaton: Automaton, provider: _FakeProvider) -> ChatService:
        automaton.set_storage_location(db.get_project_revision(PROJECT_ID))
        ai_service = AiService(provider)
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(db, project_service)
        job_service = make_test_job_service(db)
        actuator_factory = make_test_actuator_factory(db, job_service)
        tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
        return ChatService(
            ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
            session_manager=ChatSessionManager(db), tracking_service=tracking_service,
            metric_service=metric_service, job_service=job_service, actuator_factory=actuator_factory,
        )

    return make


async def _streamed_events(chat_service: ChatService, text: str) -> list[tuple[str, dict]]:
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    response = SseChatTurn(chat_service, session["id"], text).response()
    events = []
    async for frame in response.body_iterator:
        for block in frame.strip().split("\n\n"):
            event_line, data_line = block.split("\n", 1)
            events.append((event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))))
    return events


def _kinds(events: list[tuple[str, dict]]) -> list[str]:
    return [
        f"tool({data['phase']})" if event == "tool" else event
        for event, data in events
    ]


def _streamed_text(events: list[tuple[str, dict]]) -> str:
    return "".join(data["content"] for event, data in events if event == "chunk")


async def test_with_declared_sources_every_chunk_of_the_replayed_final_round_precedes_done(chat_service_for):
    chat_service = chat_service_for(
        _automaton(with_sources=True, autotracking_on_ai_message=True), _FakeProvider(tool_rounds=1),
    )

    events = await _streamed_events(chat_service, "where's my flight?")

    kinds = _kinds(events)
    assert kinds[:2] == ["tool(start)", "tool(result)"]
    assert kinds[-1] == "done"
    chunk_kinds = kinds[2:-1]
    assert chunk_kinds and set(chunk_kinds) == {"chunk"}
    assert _streamed_text(events) == "Your flight is on time."
    assert events[-1][1]["reply"][0]["content"] == "Your flight is on time."


async def test_without_sources_and_tracking_after_the_user_message_every_chunk_precedes_done(chat_service_for):
    chat_service = chat_service_for(
        _automaton(with_sources=False, autotracking_on_ai_message=False), _FakeProvider(tool_rounds=0),
    )

    events = await _streamed_events(chat_service, "hello")

    kinds = _kinds(events)
    assert kinds[-1] == "done"
    assert kinds[:-1] and set(kinds[:-1]) == {"chunk"}
    assert _streamed_text(events) == "Your flight is on time."


async def test_with_declared_sources_but_no_tool_call_the_answer_streams_then_done(chat_service_for):
    chat_service = chat_service_for(
        _automaton(with_sources=True, autotracking_on_ai_message=True), _FakeProvider(tool_rounds=0),
    )

    events = await _streamed_events(chat_service, "hello")

    kinds = _kinds(events)
    assert kinds[-1] == "done"
    assert kinds[:-1] and set(kinds[:-1]) == {"chunk"}
    assert _streamed_text(events) == "Your flight is on time."
