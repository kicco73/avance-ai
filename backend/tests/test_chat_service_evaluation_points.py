"""Integration-ish tests for how a real chat turn links a Tracking row to
a message (see tracking/tracking_processor.py's TrackingProcessor.process/
_move_automaton, db.link_signal_to_message) — and for the
expected_state/expected_values annotation writes that only a message with
such a link allows (see ChatService.set_message_expected_state/
set_message_expected_signals).

Current contract (verified directly against tracking/tracking_processor.py,
tracking/tracking_processor_user.py, tracking/tracking_processor_ai.py):
  - A Tracking row is only ever created when a trigger actually FIRES a
    transition (tracking_processor.py's `_move_automaton` is only called
    from a fired-action branch in both subclasses) — merely *evaluating*
    signals with nothing meeting a trigger's threshold leaves no row at
    all, in either autotracking mode.
  - That row is linked to whichever message actually *caused* the
    evaluation that fired it — the Edit Project timeline positions a
    transition marker by that link (see frontend/src/benchmarkTimeline.js's
    effectiveTimestamp), so linking it to the wrong message visibly
    misplaces the marker. "before" mode (autotracking_on_ai_message=False)
    decides the trigger from the user's own message, already saved before
    the reply is even generated, so it's linked to the **user's** message
    (tracking_processor_user.py's own `apply_transition(..., message_id=
    self.user.message_id)`). "after" mode (autotracking_on_ai_message=True)
    decides the trigger from the assistant's own reply, so it's linked to
    the **assistant's** message instead (tracking_processor.py's own
    post-hoc `link_signal_to_message(tracking_id, assistant_id)`, which
    only ever fires when the row wasn't already linked at creation — see
    OutVariables.tracking_linked_to_message).
  - `Automaton` now carries a single flag, `autotracking_on_ai_message`
    (the old, separate `autotracking_on_user_message` flag was removed —
    see tracking_service.py:193, `if not automaton.autotracking_on_ai_message`):
    `False` (default) selects `TrackingProcessorAfterUserMessage` ("before"
    mode, the optimistic pre-reply guess), `True` selects
    `TrackingProcessorAfterAiMessage` ("after" mode) — always exactly one
    of the two processors, never neither.
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
from db.models import Tracking
from jobs import JobQueue, PersistedJobSink
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
    """Stands in for project.project_service.ProjectService — just enough
    of its interface for ChatService to run a real turn against a fixed,
    hand-built automaton (see _automaton above), no file/YAML involved."""

    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

    def get_project_availability(self, project_name: str):
        return (False, None)


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
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)

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
            persisted_jobs=JobQueue(PersistedJobSink(db), max_concurrent=1),
        )
        return service

    return make


async def _bootstrap_session(chat_service: ChatService) -> int:
    session = chat_service.get_or_create_current_session(None)
    return session["id"]


@pytest.mark.regression
async def test_transition_from_optimistic_guess_links_the_causing_user_message(db, chat_service_for):
    # "before" mode (autotracking_on_ai_message=False) is optimistic (see
    # tracking_processor_user.py's own module docstring): the reply is
    # generated once, using the *current* state's own context, and its
    # own embedded signals decide whether a transition fires — here foo=1
    # satisfies "foo >= 0", so the guess turns out wrong and a second,
    # regenerated reply (against state "b"'s own context) is what
    # actually gets used. Only 2 calls, never a 3rd/cascading one:
    # chat_service.process_turn (chat/chat_service.py:470-478) never
    # calls _messages_for_transition for an ordinary chat turn — that's
    # only reachable from apply_manual_action/session bootstrap, not from
    # here.
    ai_service = FakeSchemaAiService([{"signals": '{"foo": 1}'}, {"signals": '{"foo": 1}'}])
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=False), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn(session_id, "hello")

    assert ai_service.call_count == 2
    assert result["new_state"] == "b"
    # The row lands on the user's own message — that's the one whose
    # optimistic evaluation actually decided the transition fired, before
    # the assistant's reply was even regenerated (see tracking_processor_
    # user.py's own apply_transition(..., message_id=self.user.message_id)
    # call). The Edit Project timeline positions the state-change marker
    # by this exact link (see frontend/src/benchmarkTimeline.js's
    # effectiveTimestamp) — landing it on the assistant's message instead
    # would visibly place the marker one turn too late.
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
    # queryable row (see tracking_engine.py's apply_transition — action is
    # None here, so it lands in the plain-snapshot branch) — linked to the
    # user's own message, the one whose content decided nothing should
    # fire (see tracking_processor_user.py's own apply_transition(...,
    # message_id=self.user.message_id) call, now unconditional on signals
    # having been evaluated at all, not just on a transition firing).
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
    # actually reported for its own turn at all (see this file's module
    # docstring) — a trigger that simply never meets its own threshold
    # still leaves a plain-snapshot evaluation point now (see
    # test_user_message_autotracking_makes_a_single_ai_call_when_the_
    # optimistic_guess_is_right), so this needs a turn where the model
    # reports no signals whatsoever — the one scenario that genuinely
    # leaves a message unlinked.
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
    """Regression test, pinned against a real reported scenario (a live
    "before" mode project, autotracking_on_ai_message=False — a session
    bootstrap plus one real user turn that fires a transition): every
    Tracking row this machinery can produce must end up linked to the
    message that actually caused it, never to a temporally-adjacent
    (previous or next) one — and every real evaluation must leave a row
    at all, even one that never fired. Wrong data confirmed directly
    against a live avance.db before this fix: the init row was linked to
    the *opening* message (should be unlinked — there's no causing
    message for it yet), both [env]-only rows (the opening message's own,
    and the regenerated reply's own) were left completely unlinked
    (should each point at the very message that reported them), and the
    opening message's own signal evaluation left no row at all (should
    leave a plain snapshot, same as the real turn's own non-firing
    optimistic guess would).

    The 5 Tracking rows this produces, in order:
      1. the init ("" -> "a") transition (see ChatService.open_if_needed)
         — never linked to a message at all (see its own docstring:
         nothing has caused it yet at that point).
      2. the opening message's own signal evaluation — foo=-1 never
         satisfies "foo >= 0", so no transition fires, but the evaluation
         itself still leaves a plain snapshot (see tracking_engine.py's
         apply_transition: action is None here), linked to the opening
         message.
      3. the env-only row the opening AI message itself reported via
         [env] — linked to that same opening message (see
         TrackingProcessor.process's env.update(..., message_id=assistant_id)).
      4. the real ("a" -> "b") transition — decided from the user's own
         message, before the AI's reply is even regenerated (see
         TrackingProcessorAfterUserMessage._get_ai_reply's
         apply_transition(..., message_id=self.user.message_id)) — so
         it's linked to the *user's* message, not the assistant's.
      5. the env-only row the regenerated reply itself reported via
         [env] — linked to that reply message, same mechanism as row 3.
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
    # Regression test: ChatService.process_turn used to call
    # self._session_manager.touch_session(reply['session_id'], reply['state'])
    # — reply['state'] is the full StatePayload dict (see
    # _build_turn_response), not a string. touch_session's own
    # db.touch_chat_session writes that argument straight into
    # ChatSession.end_state's CharField, so passing the whole dict
    # silently stored its Python repr there instead of just the state
    # key — visible in the Sessions panel as a rendered-dict title.
    # apply_manual_action already got this right (touch_session(...,
    # state.key)); process_turn now matches.
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn(session_id, "hello")

    assert result["new_state"] == "b"
    session = db.get_chat_session(session_id)
    assert session["end_state"] == "b"
