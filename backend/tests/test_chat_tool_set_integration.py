"""End-to-end: a state's own `ai-may-read-sources:` (see automaton.
State.ai_may_read_sources) produces a real, working ToolSet, threaded all the way from
TrackingProcessor down to AiService — proven with a fake AiService that
actually drives it mid-turn, resolving a real source.<name>.select()
call against a real (file-backed) archive.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Source, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import make_test_actuator_factory, make_test_job_service
from db.db import Db
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

PROJECT_ID = "proj"


class FakeToolAwareAiService:
    """Like test_chat_service_evaluation_points.py's own
    FakeSchemaAiService, but — when handed a real ToolSet — actually
    calls it once before "answering", the way a real provider asking for
    exactly one tool and then completing the turn would. Proves the
    ToolSet TrackingProcessor built is the real thing, not a stand-in:
    its own call() resolves against the real automaton/db/session."""

    def __init__(self, metadata_per_call: list[dict], tool_call: tuple[str, dict] | None = None) -> None:
        self._metadata_per_call = metadata_per_call
        self._tool_call = tool_call
        self.call_count = 0
        self.tool_results: list[str] = []

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema, tool_set=None):
        if self._tool_call is not None and tool_set is not None and self.call_count == 0:
            name, arguments = self._tool_call
            result = await tool_set.call(name, arguments)
            self.tool_results.append(result)
            # Real AiService emits this unconditionally, not gated by
            # schema (see its own tool-call loop) — TrackingProcessor's
            # own on_metadata handler is what persists it to Tracking.tool_calls.
            on_metadata("tool_result", {"name": name, "arguments": arguments, "result": result})
        index = min(self.call_count, len(self._metadata_per_call) - 1)
        metadata = self._metadata_per_call[index]
        self.call_count += 1
        for key, value in metadata.items():
            if key in schema:
                on_metadata(key, value)
        yield "Hi!"


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state(self, project_id: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_for_session(self, session_id: int):
        return self._automaton

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_published_revision(self, project_id: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return False

    def get_project_availability(self, project_id: str):
        return (False, None)


@pytest.fixture
def file_db(tmp_path) -> Db:
    # File-backed, not :memory: — a state's own ai-may-read-sources
    # call the driver via ToolSet.call's own asyncio.to_thread, and a second thread's
    # connection to ":memory:" would see a distinct, empty database
    # instead of shared state (see test_tool_set.py's own file_db).
    return Db(f"sqlite:///{tmp_path / 'chat_tool_set.db'}")


def _automaton_with_a_tool() -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
        ai_may_read_sources=("flights",),
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a}
    automaton = Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], attachments={},
        general_attachments={}, autotracking_on_ai_message=True,
        sources=[Source(name="flights", url="avance:flights.csv", ui_label="Flights", ai_definition="One row per flight.")],
        project_id=PROJECT_ID,
    )
    return automaton


@pytest.fixture
def chat_service_for(file_db):
    file_db.ensure_project(PROJECT_ID)
    file_db.save_project_files(PROJECT_ID, {"flights.csv": b"city,country\nParis,France\n"}, {"flights.csv": "text/csv"})
    file_db.publish_project(PROJECT_ID)

    def make(automaton: Automaton, *, ai_service) -> ChatService:
        # AvanceArchiveSource needs to know where to actually read from
        # (see Automaton.set_storage_location) — a hand-built Automaton
        # in a test has no revision until told, unlike one AutomatonLoader
        # would have already resolved off a real publish.
        automaton.set_storage_location(file_db.get_project_revision(PROJECT_ID))
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(file_db, project_service)
        job_service = make_test_job_service(file_db)
        actuator_factory = make_test_actuator_factory(file_db, job_service)
        tracking_service = TrackingService(file_db, project_service, metric_service, actuator_factory)
        return ChatService(
            ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=file_db,
            session_manager=ChatSessionManager(file_db), tracking_service=tracking_service,
            metric_service=metric_service, job_service=job_service, actuator_factory=actuator_factory,
        )

    return make


async def _bootstrap_session(chat_service: ChatService) -> int:
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    return session["id"]


@pytest.mark.regression
async def test_a_real_chat_turn_resolves_a_tool_call_against_the_state_s_own_declared_source(chat_service_for):
    ai_service = FakeToolAwareAiService(
        [{"memory": "stage: greeted"}], tool_call=("source_flights_select", {"values": ["paris"]}),
    )
    chat_service = chat_service_for(_automaton_with_a_tool(), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn(session_id, "where's my flight to Paris?")

    assert result["reply"] == []  # FakeToolAwareAiService.generate_stream_with_metadata's own "Hi!" chunk
    assert ai_service.tool_results == ["city,country\nParis,France\n"]


@pytest.mark.regression
async def test_a_real_chat_turn_persists_its_own_tool_calls_onto_the_assistant_message(chat_service_for, file_db):
    ai_service = FakeToolAwareAiService(
        [{"memory": "stage: greeted"}], tool_call=("source_flights_select", {"values": ["paris"]}),
    )
    chat_service = chat_service_for(_automaton_with_a_tool(), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn(session_id, "where's my flight to Paris?")

    tool_calls_by_message = file_db.get_tool_calls_by_message(session_id)
    assert tool_calls_by_message[result["assistant_message_id"]] == [
        {"name": "source_flights_select", "arguments": {"values": ["paris"]}, "result": "city,country\nParis,France\n"},
    ]


@pytest.mark.regression
async def test_get_messages_surfaces_the_persistent_tool_call_summary_on_reload(chat_service_for, file_db):
    """The permanent "Searched … · N rows" line a real AiService's own
    tool-call loop folds into 'tool_result' as summary_text (see
    ToolSet.summary_text) rides along in Tracking.tool_calls with no
    separate storage of its own — ChatService.get_messages must surface
    it again on every reload, keyed to the right message."""
    ai_service = FakeToolAwareAiService([{"memory": "stage: greeted"}])
    chat_service = chat_service_for(_automaton_with_a_tool(), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    assistant_message_id = file_db.save_message("assistant", "Paris it is.", session_id)
    tool_call_entry = {
        "name": "source_flights_select", "arguments": {"values": ["paris"]},
        "result": "city,country\nParis,France\n", "summary_text": 'Searched Flights for "paris" · 1 row',
    }
    file_db.record_tool_calls(session_id, [tool_call_entry], message_id=assistant_message_id)

    messages = await chat_service.get_messages(session_id)

    reloaded = next(m for m in messages if m["id"] == assistant_message_id)
    assert reloaded["tool_calls"] == [tool_call_entry]


@pytest.mark.regression
async def test_get_messages_omits_tool_calls_for_a_message_with_none(chat_service_for, file_db):
    ai_service = FakeToolAwareAiService([{"memory": "stage: greeted"}])
    chat_service = chat_service_for(_automaton_with_a_tool(), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    plain_message_id = file_db.save_message("assistant", "no tools here", session_id)

    messages = await chat_service.get_messages(session_id)

    reloaded = next(m for m in messages if m["id"] == plain_message_id)
    assert "tool_calls" not in reloaded


def test_build_tool_set_is_none_for_a_state_with_neither_field(file_db):
    # The init state ("") declares none of the three fields — build_tool_set must return
    # None for it, so a fake that only answers once tool_set is real
    # (like the one the end-to-end test above uses) never gets called at
    # all for a state without one.
    from tracking.tracking_processor import TrackingProcessor, UserVariables

    automaton = _automaton_with_a_tool()
    user = UserVariables(automaton=automaton, state=automaton.states[""], project_id=PROJECT_ID, session_id=1)
    processor = TrackingProcessor.__new__(TrackingProcessor)
    processor.db = file_db
    processor.user = user

    assert processor.build_tool_set(automaton.states[""]) is None
    assert processor.build_tool_set(automaton.states["a"]) is not None
