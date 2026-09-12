"""A turn engine standing on its own, for tests that drive one directly.

Here rather than inside whichever test module happened to write it first:
these pieces are core — an Automaton, a TurnService, a project service
that answers with one fixed automaton — and a skill's tests use them
without the skill owning them. A test module never imports another test
module, so moving a test into the package it belongs to cannot break one
that stayed behind.
"""
from __future__ import annotations

import asyncio

import pytest

from ai.ai_service import AiService
from automaton.automaton import Action, Automaton, Source, State
from db.db import Db
from metrics.metric_service import MetricService
from turn.sessions.session_manager import SessionManager
from turn.turn_service import TurnService
from tracking.tracking_service import TrackingService

from conftest import make_test_namespace_factory, make_test_scheduler_service

PROJECT_ID = "proj"


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


def one_state_automaton(*, with_sources: bool, autotracking_on_ai_message: bool) -> Automaton:
    """One state the user can only advance out of, optionally declaring a
    source the AI may read — the smallest automaton a turn can run on."""
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
        ai_may_read_sources=("flights",) if with_sources else (),
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a}
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], general_attachments={},
        autotracking_on_ai_message=autotracking_on_ai_message,
        sources=[Source(name="flights", url="avance:flights.csv", ui_label="Flights", ai_definition="One row per flight.")]
        if with_sources else [],
        project_id=PROJECT_ID,
    )


@pytest.fixture
def turn_service_for(tmp_path):
    db = Db(f"sqlite:///{tmp_path / 'turn_harness.db'}")
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"flights.csv": b"city,country\nParis,France\n"}, {"flights.csv": "text/csv"})
    db.publish_project(PROJECT_ID)

    def make(automaton: Automaton, provider) -> TurnService:
        automaton.set_storage_location(db.get_project_revision(PROJECT_ID))
        ai_service = AiService(provider)
        project_service = FakeProjectService(automaton)
        metric_service = MetricService(db, project_service)
        scheduler_service = make_test_scheduler_service(db)
        namespace_factory = make_test_namespace_factory(db, scheduler_service)
        tracking_service = TrackingService(db, project_service, metric_service, namespace_factory)
        return TurnService(
            ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
            session_manager=SessionManager(db), tracking_service=tracking_service,
            metric_service=metric_service, scheduler_service=scheduler_service, namespace_factory=namespace_factory,
        )

    # The very database those services write to — what a test asserts the
    # persisted order of messages against.
    make.db = db
    return make


#: Every type a turn publishes, and what a test collects to see what one
#: did — see turn/input_listener.py.
TURN_FRAMES = (
    "output.text_stream", "output.text", "output.speech", "output.tool", "output.reaction",
    "state.changed", "ui.buttons", "output.error",
)
def _is_terminal(kinds: list[str]) -> bool:
    """The answer is the `output.text` published after `ui.buttons` — an
    earlier one is a message the state owed before it could answer. An
    `output.error` replaces the answer and ends the exchange too."""
    return kinds[-1] == "output.error" or (kinds[-1] == "output.text" and "ui.buttons" in kinds)


async def drive_turn(turn_service, db, session_id: int, _unused: str, text: str) -> list[tuple[str, dict]]:
    """One turn, through the real listener, with the frames it published.

    The listener runs a turn as its own task — a channel that awaited one
    would hold up everyone else on the Bus — so there is nothing to await
    from outside and the terminal frame is what says it is over. Frames
    are picked out by the session they belong to.
    """
    from system import bus
    from system.bus import INPUT_TEXT, Message
    from system.web_session import WebSession
    from turn.input_listener import TurnInput

    # The listener looks the sender's role up rather than taking it off
    # the wire (see Session.for_sender), so the sender has to exist.
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)

    collected: list = []
    finished = asyncio.Event()

    async def take(message) -> None:
        if message.session_id != session_id:
            return
        collected.append(message)
        if _is_terminal([m.type for m in collected]):
            finished.set()

    for message_type in TURN_FRAMES:
        bus.subscribe(message_type, take)
    if not any(getattr(listener, "__self__", None).__class__ is TurnInput for listener in bus.handlers_for(INPUT_TEXT)):
        TurnInput(turn_service, db).register()
    try:
        await bus.publish(Message(
            type=INPUT_TEXT, body={"text": text}, username=WebSession().user, session_id=session_id,
            channel="webchat", origin_id=f"connection-{_unused}",
        ))
        await asyncio.wait_for(finished.wait(), timeout=10)
    finally:
        for message_type in TURN_FRAMES:
            bus.unsubscribe(message_type, take)
    return [(m.type, m.body if isinstance(m.body, dict) else {"body": m.body}) for m in collected]
