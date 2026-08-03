"""Integration-ish tests for how a real chat turn links a Tracking row to
the message that caused it (see chat.turn_processor.TurnProcessor.process/
_run_auto_tracking, db.link_signal_to_message) — and for the
expected_state/expected_values annotation writes that only a message with
such a link allows (see ChatService.set_message_expected_state/
set_message_expected_signals).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from chat.chat_service import ChatService, ChatServiceError
from chat.session_manager import ChatSessionManager
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService, TrackingServiceError

PROJECT_NAME = "proj"


def _automaton(*, autotracking_on_user_message=False, autotracking_on_ai_message=False) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="foo >= 0")
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


def _chained_automaton(*, autotracking_on_user_message=False, autotracking_on_ai_message=False) -> Automaton:
    """a -[foo>=0]-> b -[foo>=10]-> c — two independent triggers, one per
    hop, so a test can make the first hop fire off the optimistic
    user-message guess and the second fire off the very next
    autotracking_on_ai_message check on the regenerated reply (see
    TurnProcessor.process's own docstring: "no special-casing" — a
    second transition cascading off the first is just an ordinary
    autotracking_on_ai_message evaluation, nothing bespoke)."""
    action_ab = Action(name="advance", ui_label="Advance", ui_button="Advance", target="b", trigger="foo >= 0")
    action_bc = Action(name="advance2", ui_label="Advance2", ui_button="Advance2", target="c", trigger="foo >= 10")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi a", actions=[action_ab])
    state_b = State(key="b", ui_label="B", final=False, contextual_prompt="hi b", actions=[action_bc])
    state_c = State(key="c", ui_label="C", final=True, actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": state_a,
        "b": state_b,
        "c": state_c,
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


class TaggedAiService:
    """Like conftest.py's FakeAiService, but the reply embeds a real
    [signals]{...}[/signals] tag — so AutoTracker.run() gets a genuinely
    non-empty signal_values dict straight from the metadata (see
    MetadataHandler._parse_metadata_tag) and never falls back to actually
    calling the AI a second time to compute signals from scratch."""

    def __init__(self, signals: dict) -> None:
        self._reply = f'Hi![signals]{signals!r}[/signals]'.replace("'", '"')

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    async def generate(self, system_prompt, history, on_retry=None) -> str:
        return self._reply

    async def generate_stream(self, system_prompt, history, on_retry=None):
        yield self._reply

    def supports_metadata(self) -> bool:
        return False


class SequentialAiService:
    """Like TaggedAiService, but returns a queued [signals]-tagged reply
    per call, in order (the last one repeats if called more times than
    queued) — lets a test give the *first* call (current-state context,
    see TurnProcessor.process's own optimistic guess) a different signal
    report than a *second*, post-transition redo call gets, without
    needing to actually parse/distinguish the two prompts. call_count is
    the one thing every test in this file cares about proving: the whole
    point of the optimistic guess is making exactly one call in the
    common (no-transition) case instead of two."""

    def __init__(self, signal_reports: list[dict]) -> None:
        self._replies = [f'Hi![signals]{report!r}[/signals]'.replace("'", '"') for report in signal_reports]
        self.call_count = 0

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    async def generate(self, system_prompt, history, on_retry=None) -> str:
        reply = self._replies[min(self.call_count, len(self._replies) - 1)]
        self.call_count += 1
        return reply

    async def generate_stream(self, system_prompt, history, on_retry=None):
        yield await self.generate(system_prompt, history, on_retry=on_retry)

    def supports_metadata(self) -> bool:
        return False


@pytest.fixture
def chat_service_for(db):
    def make(automaton: Automaton, *, signal_values: dict = {"foo": 1}, ai_service=None) -> ChatService:
        ai_service = ai_service or TaggedAiService(signal_values)
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(
            db, get_username=lambda: "user", get_active_project_name=lambda: PROJECT_NAME,
        )
        tracking_service = TrackingService(
            db, ai_service, metric_service,
            get_active_automaton=lambda: project_service.get_active_automaton_and_state()[0],
            get_username=lambda: "user",
            get_active_project_name=lambda: PROJECT_NAME,
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


async def test_user_message_evaluation_is_linked_to_the_user_message(db, chat_service_for):
    # autotracking_on_user_message is optimistic (see TurnProcessor.
    # process's own module docstring): the reply is generated once,
    # using the *current* state's context, and its own embedded signals
    # decide whether a transition fires — here foo=1 satisfies "foo >=
    # 0", so the guess turns out wrong and a second, regenerated reply
    # (against state "b"'s own context) is what actually gets used. 3
    # calls, not 2: state "b" is `final=True`, which _should_generate_
    # opening_message treats as chat-blocked, so entering it also
    # generates its own real opening message (see chat_service.py's
    # _messages_for_transition) — an ordinary, necessary call, not
    # redundant overhead from this optimistic path (see the dedicated
    # call_count test below for the common, no-transition case, where
    # no transition — hence no opening message either — ever fires).
    ai_service = SequentialAiService([{"foo": 1}, {"foo": 1}])
    chat_service = chat_service_for(_automaton(autotracking_on_user_message=True), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    await chat_service.process_turn("hello", session_id)

    assert ai_service.call_count == 3
    user_message = next(m for m in db.get_messages(session_id) if m["role"] == "user")
    linked = db.get_signal_row_by_message(user_message["id"])
    assert linked is not None
    assert linked["new_state"] == "b"


async def test_user_message_autotracking_makes_a_single_ai_call_when_the_optimistic_guess_is_right(db, chat_service_for):
    # The common case (see TurnProcessor.process's own module docstring):
    # foo=-1 never satisfies "foo >= 0", so no transition fires and the
    # one reply already generated (with the current state's own context)
    # is simply used as-is — no second, wasted call.
    ai_service = SequentialAiService([{"foo": -1}])
    chat_service = chat_service_for(_automaton(autotracking_on_user_message=True), ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn("hello", session_id)

    assert ai_service.call_count == 1
    assert result["state_changed"] is False
    user_message = next(m for m in db.get_messages(session_id) if m["role"] == "user")
    linked = db.get_signal_row_by_message(user_message["id"])
    assert linked is not None
    assert linked["new_state"] is None


async def test_optimistic_success_does_not_redundantly_evaluate_ai_message_tracking_too(db, chat_service_for):
    # Both flags on, guess succeeds (no transition): the assistant's own
    # message must NOT get its own, separately-evaluated Tracking row —
    # its signals are the exact same numbers already validated/persisted
    # against the user's own message moments earlier (see
    # TurnProcessor._finish_turn's own skip_ai_message_tracking).
    ai_service = SequentialAiService([{"foo": -1}])
    chat_service = chat_service_for(
        _automaton(autotracking_on_user_message=True, autotracking_on_ai_message=True), ai_service=ai_service
    )
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn("hello", session_id)

    assert ai_service.call_count == 1
    user_message = next(m for m in db.get_messages(session_id) if m["role"] == "user")
    assert db.get_signal_row_by_message(user_message["id"]) is not None
    assistant_message = next(m for m in result["reply"] if m.get("id") is not None)
    assert db.get_signal_row_by_message(assistant_message["id"]) is None


async def test_transition_from_user_message_guess_can_cascade_into_a_further_ai_message_transition(db, chat_service_for):
    # Both flags on, guess turns out wrong (foo=1 fires a -> b): the
    # regenerated reply (against state "b"'s own context, call #2) reports
    # foo=20, which *itself* satisfies b's own "foo >= 10" trigger —
    # evaluated by the perfectly ordinary autotracking_on_ai_message
    # check, no special casing for the fact it followed a user-message-
    # triggered one (see TurnProcessor.process's own module docstring).
    # 3 calls, not 2: that second transition (b -> c) needs its own real
    # opening message for "c" (call #3, via chat_service.py's own
    # _messages_for_transition) — an ordinary, necessary call for
    # *landing* on a new state, same as any other transition, not
    # overhead from this optimistic path itself.
    automaton = _chained_automaton(autotracking_on_user_message=True, autotracking_on_ai_message=True)
    ai_service = SequentialAiService([{"foo": 1}, {"foo": 20}])
    chat_service = chat_service_for(automaton, ai_service=ai_service)
    session_id = await _bootstrap_session(chat_service)
    ai_service.call_count = 0  # bootstrap's own init-action opening message doesn't count

    result = await chat_service.process_turn("hello", session_id)

    assert ai_service.call_count == 3
    assert result["new_state"] == "c"
    user_message = next(m for m in db.get_messages(session_id) if m["role"] == "user")
    user_linked = db.get_signal_row_by_message(user_message["id"])
    assert user_linked is not None
    assert user_linked["new_state"] == "b"
    assistant_message = next(m for m in result["reply"] if m.get("id") is not None)
    assistant_linked = db.get_signal_row_by_message(assistant_message["id"])
    assert assistant_linked is not None
    assert assistant_linked["new_state"] == "c"


async def test_ai_message_evaluation_is_linked_to_the_assistant_message(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn("hello", session_id)

    assistant_message = next(m for m in result["reply"] if m.get("id") is not None)
    stored = db.get_message(assistant_message["id"])
    assert stored["role"] == "assistant"

    linked = db.get_signal_row_by_message(assistant_message["id"])
    assert linked is not None
    assert linked["new_state"] == "b"  # the trigger fired: foo >= 0


async def test_no_autotracking_means_no_linked_signal_row_at_all(db, chat_service_for):
    chat_service = chat_service_for(_automaton())  # both autotracking flags default False
    session_id = await _bootstrap_session(chat_service)

    result = await chat_service.process_turn("hello", session_id)

    assistant_message = next(m for m in result["reply"] if m.get("id") is not None)
    assert db.get_signal_row_by_message(assistant_message["id"]) is None


async def test_set_message_expected_state_on_a_real_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    updated = chat_service.set_message_expected_state(message_id, "a")
    assert updated["expected_state"] == "a"

    cleared = chat_service.set_message_expected_state(message_id, None)
    assert cleared["expected_state"] is None


async def test_set_message_expected_state_rejects_an_unknown_state(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_state(message_id, "not-a-real-state")


async def test_set_message_expected_state_rejects_a_non_evaluation_point_message(db, chat_service_for):
    chat_service = chat_service_for(_automaton())  # no autotracking, so nothing is annotatable
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_state(message_id, "a")


async def test_set_message_expected_signals_on_a_real_evaluation_point(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    updated = chat_service.set_message_expected_signals(message_id, {"foo": 75})
    assert updated["expected_values"] == '{"foo": 75}'
    # The actually-observed values must stay untouched.
    assert updated["values"] is not None

    cleared = chat_service.set_message_expected_signals(message_id, None)
    assert cleared["expected_values"] is None


async def test_set_message_expected_signals_rejects_an_unknown_signal_name(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_signals(message_id, {"not-a-real-signal": 50})


async def test_set_message_expected_signals_rejects_an_out_of_range_value(db, chat_service_for):
    chat_service = chat_service_for(_automaton(autotracking_on_ai_message=True))
    session_id = await _bootstrap_session(chat_service)
    result = await chat_service.process_turn("hello", session_id)
    message_id = next(m for m in result["reply"] if m.get("id") is not None)["id"]

    with pytest.raises(TrackingServiceError):
        chat_service.set_message_expected_signals(message_id, {"foo": 150})
