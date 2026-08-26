"""Tests linking a Tracking row to a message on a real chat turn, and the
expected_state/expected_values annotation writes that link allows. A
Tracking row is only created when a trigger fires a transition, linked to
whichever message caused the firing evaluation ("before" vs "after" mode)."""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from db.models import Tracking
from conftest import NullBroadcaster
from jobs import JobQueue
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService, TrackingServiceError

pytestmark = pytest.mark.asyncio

PROJECT_NAME = "proj"


def _automaton(*, autotracking_on_ai_message=False, trigger="signal.foo >= 0") -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger=trigger)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": state_a,
        "b": state_b,
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[Signal(name="foo", ui_label="Foo", definition="foo definition")],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=autotracking_on_ai_message,
    )


class FakeProjectService:
    """Stands in for ProjectService — just enough for ChatService to run
    a real turn against a fixed, hand-built automaton, no file/YAML
    involved."""

    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state(self, project_name: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_for_session(self, session_id: int):
        return self._automaton

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

    def get_published_revision(self, project_name: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_name: str) -> bool:
        return False

    def get_project_availability(self, project_name: str):
        return (False, None)


class FakeSchemaAiService:
    """A v2-shaped (schema) fake — reports metadata straight through
    `on_metadata`, so injecting a `signals` value never depends on any
    tag-scanning. This file's subject is TrackingService/TrackingProcessor
    orchestration, not tag parsing."""

    def __init__(self, metadata_per_call: list[dict]) -> None:
        self._metadata_per_call = metadata_per_call
        self.call_count = 0

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        index = min(self.call_count, len(self._metadata_per_call) - 1)
        metadata = self._metadata_per_call[index]
        self.call_count += 1
        for key, value in metadata.items():
            on_metadata(key, value)
        yield "Hi!"


@pytest.fixture
def chat_service_for(db):
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)

    def make(automaton: Automaton, *, ai_service=None) -> ChatService:
        ai_service = ai_service or FakeSchemaAiService([{"signals": '{"foo": 1}'}])
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(db, project_service)
        job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
        tracking_service = TrackingService(
            db, ai_service, project_service, metric_service,
        )
        service = ChatService(
            ai_service=ai_service,
            project_service=project_service,
            db=db,
            session_manager=ChatSessionManager(db),
            tracking_service=tracking_service,
            metric_service=metric_service,
            job_queue=job_queue,
        )
        return service

    return make


async def _bootstrap_session(chat_service: ChatService) -> int:
    session = chat_service.get_or_create_current_session(None)
    return session["id"]


@pytest.mark.regression
async def test_transition_from_optimistic_guess_links_the_causing_user_message(db, chat_service_for):
    # "before" mode generates a reply once against the current state's
    # context; here foo=1 satisfies "foo >= 0", so the guess turns out
    # wrong and a second, regenerated reply (against state "b") is used.
    ai_service = FakeSchemaAiService([{"signals": '{"foo": 1}'}, {"signals": '{"foo": 1}'}])
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=False), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn(session_id, "hello")

    assert ai_service.call_count == 2
    assert result["new_state"] == "b"
    # The row lands on the user's message — the one whose optimistic
    # evaluation decided the transition fired, before the reply was
    # even regenerated.
    assert db.get_signal_row_by_message(result["assistant_message_id"]) is None
    linked = db.get_signal_row_by_message(result["user_message_id"])
    assert linked is not None
    assert linked["new_state"] == "b"


@pytest.mark.regression
async def test_user_message_autotracking_makes_a_single_ai_call_when_the_optimistic_guess_is_right(db, chat_service_for):
    # The common case: foo=-1 never satisfies "foo >= 0", so no transition
    # fires and the one reply already generated (with the current state's
    # own context) is simply used as-is — no second, wasted call.
    ai_service = FakeSchemaAiService([{"signals": '{"foo": -1}'}])
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=False), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn(session_id, "hello")

    assert ai_service.call_count == 1
    assert result["state_changed"] is False
    # No transition fired, but the evaluation itself still leaves a real,
    # queryable row, linked to the user's message whose content decided
    # nothing should fire.
    row = db.get_signal_row_by_message(result["user_message_id"])
    assert row is not None
    assert row["old_state"] is None and row["new_state"] is None
    assert db.get_signal_row_by_message(result["assistant_message_id"]) is None


@pytest.mark.regression
async def test_ai_message_evaluation_is_linked_to_the_assistant_message(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn(session_id, "hello")

    stored = db.get_message(result["assistant_message_id"])
    assert stored["role"] == "assistant"

    linked = db.get_signal_row_by_message(result["assistant_message_id"])
    assert linked is not None
    assert linked["new_state"] == "b"  # the trigger fired: foo >= 0


@pytest.mark.regression
async def test_set_message_expected_state_on_a_real_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn(session_id, "hello")
    message_id = result["assistant_message_id"]

    updated = chat_service.set_message_expected_state(message_id, "a")
    assert updated["expected_state"] == "a"

    cleared = chat_service.set_message_expected_state(message_id, None)
    assert cleared["expected_state"] is None


@pytest.mark.regression
async def test_set_message_expected_state_rejects_an_unknown_state(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn(session_id, "hello")
    message_id = result["assistant_message_id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_state(message_id, "not-a-real-state")


@pytest.mark.contract
async def test_set_message_expected_state_rejects_a_non_evaluation_point_message(db, chat_service_for):
    # A message only becomes an evaluation point when signals were
    # reported for its turn at all, so this needs a turn where the model
    # reports no signals whatsoever.
    ai_service = FakeSchemaAiService([{}])
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn(session_id, "hello")
    message_id = result["assistant_message_id"]
    assert db.get_signal_row_by_message(message_id) is None

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_state(message_id, "a")


@pytest.mark.regression
async def test_set_message_expected_signals_on_a_real_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn(session_id, "hello")
    message_id = result["assistant_message_id"]

    updated = chat_service.set_message_expected_signals(message_id, {"foo": 75})
    assert updated["expected_values"] == '{"foo": 75}'
    # The actually-observed values must stay untouched.
    assert updated["values"] is not None

    cleared = chat_service.set_message_expected_signals(message_id, None)
    assert cleared["expected_values"] is None


@pytest.mark.regression
async def test_set_message_expected_signals_rejects_an_unknown_signal_name(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn(session_id, "hello")
    message_id = result["assistant_message_id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_signals(message_id, {"not-a-real-signal": 50})


@pytest.mark.regression
async def test_set_message_expected_signals_rejects_an_out_of_range_value(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn(session_id, "hello")
    message_id = result["assistant_message_id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_signals(message_id, {"foo": 150})


@pytest.mark.regression
async def test_message_linking_end_to_end_bootstrap_and_one_real_turn(db, chat_service_for):
    """Regression, covering a bootstrap plus one real user turn that fires
    a transition: every Tracking row must link to the message that
    actually caused it, never a temporally-adjacent one, and every real
    evaluation must leave a row even when it never fires.

    The 5 rows this produces, in order: (1) the init transition, unlinked;
    (2) the opening message's own non-firing signal evaluation; (3) the
    env-only row the opening message reported; (4) the real transition,
    linked to the user's message, not the assistant's; (5) the env-only
    row the regenerated reply reported.
    """
    ai_service = FakeSchemaAiService([
        {"signals": '{"foo": -1}', "env": "stage: opening"},  # opening message — never fires
        {"signals": '{"foo": 1}', "env": "stage: guessed"},  # optimistic guess — fires "foo >= 0"
        {"env": "stage: crisis"},  # regenerated reply — signals never re-requested
    ])
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=False), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)

    messages = await chat_service.get_messages(session_id)  # triggers open_if_needed
    assert len(messages) == 1
    opening_message_id = messages[0]["id"]

    result = await chat_service.process_turn(session_id, "hello")
    assert result["new_state"] == "b"
    user_message_id = result["user_message_id"]
    assistant_message_id = result["assistant_message_id"]

    rows = list(Tracking.select().where(Tracking.session == session_id).order_by(Tracking.id))
    assert len(rows) == 5
    init_row, opening_snapshot_row, opening_env_row, transition_row, reply_env_row = rows

    assert (init_row.old_state, init_row.new_state) == ("", "a")
    assert init_row.message_id is None

    assert opening_snapshot_row.old_state is None and opening_snapshot_row.new_state is None
    assert opening_snapshot_row.values == '{"foo": -1}'
    assert opening_snapshot_row.message_id == opening_message_id

    assert opening_env_row.env is not None and opening_env_row.old_state is None
    assert opening_env_row.message_id == opening_message_id

    assert (transition_row.old_state, transition_row.new_state) == ("a", "b")
    assert transition_row.message_id == user_message_id

    assert reply_env_row.env is not None and reply_env_row.old_state is None
    assert reply_env_row.message_id == assistant_message_id


@pytest.mark.regression
async def test_process_turn_touches_the_session_with_the_plain_state_key_not_the_payload(db, chat_service_for):
    # Regression: touch_session's ChatSession.end_state is a CharField —
    # passing the full StatePayload dict instead of its "key" silently
    # stores a Python repr there instead of the state key.
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn(session_id, "hello")

    assert result["new_state"] == "b"
    session = db.get_chat_session(session_id)
    assert session["end_state"] == "b"
