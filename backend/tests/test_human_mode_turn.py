"""actuator.switch_to_human(user_id) takes a session out of the automaton
entirely (see ChatService._process_human_turn): no _session_scope lock, no
TrackingEngine, no auto-generated opening message — the operator's own
reply is the only thing that produces the assistant message, delivered
through the exact same 'chunk'/'done' frames a normal turn uses.
"""
from __future__ import annotations

import asyncio

import pytest

from ai.ai_service import AiService
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from chat.ws_turn import WsChatTurn
from conftest import make_test_actuator_factory, make_test_job_service
from db.db import Db
from metrics.metric_service import MetricService
from talker.base_talker import BaseTalker
from test_chat_tool_set_integration import FakeProjectService, PROJECT_ID
from test_ws_turn_event_order import _automaton
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.contract

OPERATOR = "operator@example.com"


class _FakeProvider:
    async def generate_stream_with_schema(self, *args, **kwargs):
        raise AssertionError("the model must never be called for a human-mode turn")
        yield  # pragma: no cover - never reached, keeps this an async generator

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


class _FakeHumanTalker(BaseTalker):
    """Stands in for talker.human_talker.HumanTalker: same one-chunk-then-
    the-whole-reply shape, without a real WsHumanRelay/websocket."""

    def __init__(self, reply_text: str, *, delay: "asyncio.Event | None" = None) -> None:
        self._reply_text = reply_text
        self._delay = delay

    async def chat(self, channels, chat_history, on_metadata, tool_set=None, force_required_tools=False, env_block=None):
        yield ""
        if self._delay is not None:
            await self._delay.wait()
        yield self._reply_text

    async def listen(self, audio: bytes) -> str:
        raise NotImplementedError

    def talk(self, text: str):
        raise NotImplementedError


class _RecordingConnection:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def send(self, payload: dict) -> None:
        self.frames.append(payload)


@pytest.fixture
def chat_service_for(tmp_path):
    db = Db(f"sqlite:///{tmp_path / 'human_mode.db'}")
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)

    def make(automaton, *, reply_text: str = "sure, let me check", delay_first=None):
        automaton.set_storage_location(db.get_project_revision(PROJECT_ID))
        ai_service = AiService(_FakeProvider())
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(db, project_service)
        job_service = make_test_job_service(db)
        actuator_factory = make_test_actuator_factory(db, job_service)
        tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
        calls = {"count": 0}

        def build_talker(username, session_id, session_type, project_id):
            calls["count"] += 1
            # Only the first turn's own talker waits on delay_first — a
            # second, concurrent turn must get its own independent reply
            # without ever being blocked by the first one's own wait.
            is_first = calls["count"] == 1
            return _FakeHumanTalker(reply_text, delay=delay_first if is_first else None)

        tracking_service.set_human_talker_factory(build_talker)
        service = ChatService(
            ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
            session_manager=ChatSessionManager(db), tracking_service=tracking_service,
            metric_service=metric_service, job_service=job_service, actuator_factory=actuator_factory,
        )
        return service, actuator_factory

    make.db = db
    return make


async def _run_turn(chat_service: ChatService, session_id: int, turn_id: str, text: str) -> list[tuple[str, dict]]:
    connection = _RecordingConnection()
    turn = WsChatTurn(chat_service, connection, turn_id, session_id, text)
    assert turn.accept()
    await turn.run()
    return [(frame["type"], frame) for frame in connection.frames]


async def test_a_human_operators_reply_arrives_as_the_turns_own_done_frame(chat_service_for):
    chat_service, actuator_factory = chat_service_for(
        _automaton(with_sources=False, autotracking_on_ai_message=True)
    )
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    actuator_factory.set_human_operator(session["id"], OPERATOR)

    events = await _run_turn(chat_service, session["id"], "turn-1", "hello, is anyone there?")

    kinds = [event for event, _ in events]
    # HumanTalker.chat() yields one empty chunk first (opens the bubble the
    # same way a model's first chunk would), then the whole reply.
    assert kinds == ["chunk", "chunk", "done"]
    assert events[-1][1]["reply"][0]["content"] == "sure, let me check"
    assert events[-1][1]["state_changed"] is False
    assert events[-1][1]["new_state"] is None


async def test_a_human_mode_turn_never_holds_the_session_lock(chat_service_for):
    """A second turn on the same session must not wait for the first —
    the whole point of dropping _session_scope for human mode."""
    started = asyncio.Event()
    finish = asyncio.Event()
    chat_service, actuator_factory = chat_service_for(
        _automaton(with_sources=False, autotracking_on_ai_message=True), delay_first=finish,
    )
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    actuator_factory.set_human_operator(session["id"], OPERATOR)

    async def first_turn():
        started.set()
        return await _run_turn(chat_service, session["id"], "turn-1", "first message")

    task = asyncio.create_task(first_turn())
    await started.wait()
    await asyncio.sleep(0)  # let the first turn actually reach the (would-be) lock

    # The second turn completes without waiting on the first's own reply.
    second_events = await asyncio.wait_for(
        _run_turn(chat_service, session["id"], "turn-2", "second message, sent before the first is answered"),
        timeout=1.0,
    )
    assert [event for event, _ in second_events][-1] == "done"

    finish.set()
    first_events = await asyncio.wait_for(task, timeout=1.0)
    assert [event for event, _ in first_events][-1] == "done"


async def test_a_human_mode_session_never_auto_generates_an_opening_message(chat_service_for):
    chat_service, actuator_factory = chat_service_for(
        _automaton(with_sources=False, autotracking_on_ai_message=True)
    )
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    actuator_factory.set_human_operator(session["id"], OPERATOR)

    messages = await chat_service.get_messages(session["id"])

    assert messages == []
