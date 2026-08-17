"""Integration-ish tests for how a real chat turn links a Tracking row to
a message (see tracking/tracking_processor.py's TrackingProcessor.process/
_move_automaton, db.link_signal_to_message) — and for the
expected_state/expected_values annotation writes that only a message with
such a link allows (see ChatService.set_message_expected_state/
set_message_expected_signals).

Current contract (verified directly against tracking/tracking_processor.py,
tracking/tracking_processor_user.py, tracking/tracking_processor_ai.py, and
empirically against a live TrackingService.process() call — see this
file's own git history for the probe script used):
  - A Tracking row is only ever created when a trigger actually FIRES a
    transition (tracking_processor.py's `_move_automaton` is only called
    from a fired-action branch in both subclasses) — merely *evaluating*
    signals with nothing meeting a trigger's threshold leaves no row at
    all, in either autotracking mode.
  - Whichever mode produced it, that row is always linked to the
    **assistant's** message (tracking_processor.py:100-101 — always
    `link_signal_to_message(tracking_id, assistant_id)`), never to the
    user's own message — even when it was `autotracking_on_user_message`'s
    own optimistic pre-reply guess that decided the transition fired.
  - `automaton.autotracking_on_ai_message` is not actually consulted for
    processor selection (tracking_service.py:193-196): only
    `autotracking_on_user_message` picks between the two mutually
    exclusive processors, so there is no configuration that disables
    auto-tracking altogether — one of the two processors always runs.
  - At most one transition can ever fire per `process()` call: the
    optimistic guess's own regenerated-reply pass uses a callback
    (`on_receiving_metadata_when_repeating_the_call`) that never calls
    `_would_trigger_action` again, so a second hop can't cascade within
    the same turn.
  - `process_turn`'s return dict never populates `"reply"` with message
    objects (`OutVariables.messages` is never appended to by either
    processor) — the assistant/user message ids are the dict's own
    `assistant_message_id`/`user_message_id` keys instead (ground-truth
    table row #6).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService, TrackingServiceError

pytestmark = pytest.mark.asyncio

PROJECT_NAME = "proj"


def _automaton(
    *, autotracking_on_user_message=False, autotracking_on_ai_message=False, trigger="foo >= 0"
) -> Automaton:
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
        autotracking_on_user_message=autotracking_on_user_message,
        autotracking_on_ai_message=autotracking_on_ai_message,
    )


class FakeProjectService:
    """Stands in for project.project_service.ProjectService — just enough
    of its interface for ChatService to run a real turn against a fixed,
    hand-built automaton (see _automaton above), no file/YAML involved."""

    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME


class FakeSchemaAiService:
    """A v2-shaped (schema) fake — reports metadata straight through
    `on_metadata` (see ai.ai_service.AiService.generate_stream_with_metadata),
    the same wire convention TurnProtocolUsingSchema actually drives, so
    injecting a `signals` value here never depends on
    tracking.text_filter.ConcatTagFilter's own tag-scanning at all (the
    v1/text-extraction path this file's tests used before this rewrite —
    see git history — routes every tag close through
    `asyncio.create_task(self.on_tag(...))`, which raises whenever
    `on_tag` is the plain sync callable turn_protocol_using_text_extraction.py
    actually wires up; a real, currently-reproducible bug, but not this
    file's own subject, which is TrackingService/TrackingProcessor's own
    orchestration, not tag parsing)."""

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
    def make(automaton: Automaton, *, ai_service=None) -> ChatService:
        ai_service = ai_service or FakeSchemaAiService([{"signals": '{"foo": 1}'}])
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(
            db, get_username=lambda: "user", get_active_project_name=lambda: PROJECT_NAME,
        )
        # TrackingService.__init__ now takes project_service directly, not
        # get_active_automaton/get_username/get_active_project_name
        # callables (see tracking/tracking_service.py).
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
        )
        return service

    return make


async def _bootstrap_session(chat_service: ChatService) -> int:
    session = chat_service.get_or_create_current_session(None)
    return session["id"]


@pytest.mark.contract
async def test_transition_from_optimistic_guess_links_the_resulting_assistant_message(db, chat_service_for):
    # autotracking_on_user_message is optimistic (see tracking_processor_
    # user.py's own module docstring): the reply is generated once, using
    # the *current* state's own context, and its own embedded signals
    # decide whether a transition fires — here foo=1 satisfies "foo >= 0",
    # so the guess turns out wrong and a second, regenerated reply
    # (against state "b"'s own context) is what actually gets used. Only
    # 2 calls, never a 3rd/cascading one: chat_service.process_turn
    # (chat/chat_service.py:470-478) never calls _messages_for_transition
    # for an ordinary chat turn — that's only reachable from
    # apply_manual_action/session bootstrap, not from here.
    ai_service = FakeSchemaAiService([{"signals": '{"foo": 1}'}, {"signals": '{"foo": 1}'}])
    chat_service = chat_service_for(_automaton(autotracking_on_user_message=True), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn(session_id, "hello")

    assert ai_service.call_count == 2
    assert result["new_state"] == "b"
    # The row always lands on the assistant's own message (tracking_
    # processor.py:100-101's `link_signal_to_message(tracking_id,
    # assistant_id)`) — never the user's, even though it was the user
    # message's own optimistic evaluation that decided it fired.
    assert db.get_signal_row_by_message(result["user_message_id"]) is None
    linked = db.get_signal_row_by_message(result["assistant_message_id"])
    assert linked is not None
    assert linked["new_state"] == "b"


@pytest.mark.regression
async def test_user_message_autotracking_makes_a_single_ai_call_when_the_optimistic_guess_is_right(db, chat_service_for):
    # The common case: foo=-1 never satisfies "foo >= 0", so no transition
    # fires and the one reply already generated (with the current state's
    # own context) is simply used as-is — no second, wasted call.
    ai_service = FakeSchemaAiService([{"signals": '{"foo": -1}'}])
    chat_service = chat_service_for(_automaton(autotracking_on_user_message=True), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn(session_id, "hello")

    assert ai_service.call_count == 1
    assert result["state_changed"] is False
    # No transition fired at all, so _move_automaton (the only thing that
    # ever creates a Tracking row) never ran — nothing to link, for
    # either message (tracking_processor_user.py's _get_ai_reply only
    # calls it inside its own "state changed" branch).
    assert db.get_signal_row_by_message(result["user_message_id"]) is None
    assert db.get_signal_row_by_message(result["assistant_message_id"]) is None


@pytest.mark.contract
async def test_autotracking_on_ai_message_flag_is_ignored_when_user_message_flag_is_set(db, chat_service_for):
    # Row #4 of this refactor's own ground truth: processor selection
    # (tracking_service.py:193-196) consults only
    # autotracking_on_user_message — autotracking_on_ai_message is never
    # read there at all, so setting both True behaves identically to only
    # the user-message flag being True; nothing "extra" evaluates the
    # assistant's own message a second time.
    ai_service = FakeSchemaAiService([{"signals": '{"foo": -1}'}])
    chat_service = chat_service_for(
        _automaton(autotracking_on_user_message=True, autotracking_on_ai_message=True), ai_service=ai_service
    )
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn(session_id, "hello")

    assert ai_service.call_count == 1
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
    # A message only becomes an evaluation point when its own turn
    # actually fires a transition (see this file's module docstring) —
    # "no autotracking flags" does NOT produce one (tracking_service.py's
    # processor selection always picks one of the two modes, never
    # neither — see the deleted test_no_autotracking_means_no_linked_
    # signal_row_at_all this replaces); a trigger that simply never meets
    # its own threshold is what actually leaves a message unlinked.
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True, trigger="foo >= 99"))
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
