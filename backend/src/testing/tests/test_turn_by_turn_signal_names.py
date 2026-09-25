from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from testing.signal_sources import TurnByTurnSignalSource
from tracking.env import Env

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"


class _RecordingAiService:
    def __init__(self, signals: dict | None = None) -> None:
        self.requested_signals: list[set[str]] = []
        self._signals = signals

    def is_provider_with_schema(self) -> bool:
        return True

    def get_models_snapshot(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    async def generate_stream_with_metadata(
        self, system_prompt, history, on_metadata, schema, tool_set=None, force_required_tools=False,
    ):
        self.requested_signals.append(set(schema["signals"].fields))
        if self._signals is not None:
            on_metadata("signals", self._signals)
        yield ""


def _automaton() -> Automaton:
    init_action = Action(name="init", ui_label="init", ui_button="", target="A")
    return Automaton(
        init_action=init_action,
        states={
            "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
            "A": State(input_processor="ai", key="A", ui_label="A", final=False, actions=[
                Action(name="to_b", ui_label="to_b", ui_button="", target="B", trigger="signal.alpha > 50"),
            ]),
            "B": State(input_processor="ai", key="B", ui_label="B", final=False, actions=[
                Action(name="to_a", ui_label="to_a", ui_button="", target="A", trigger="signal.beta > 50"),
            ]),
        },
        general_prompt="general",
        signals=[
            Signal(name="alpha", ui_label="Alpha", definition="whatever"),
            Signal(name="beta", ui_label="Beta", definition="whatever"),
        ],
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def _conversation(db) -> tuple[int, int]:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username="user", project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), start_state="A",
    )
    user_message_id = db.save_message("user", "hi", session_id)
    db.save_message("assistant", "hello", session_id)
    return session_id, user_message_id


def _tracking_row(db, session_id, message_id, *, new_state=None, expected_state=None) -> None:
    db.import_tracking_row(
        session_id, old_state=None, action=None, new_state=new_state, values=None,
        expected_state=expected_state, expected_values=None, comment=None,
        message_id=message_id, timestamp=datetime(2026, 1, 1),
    )


def _source(db, session_id, ai_service) -> TurnByTurnSignalSource:
    return TurnByTurnSignalSource(
        ai_service=ai_service, tracking_service=None, db=db, automaton=_automaton(), session_id=session_id,
        env=Env(), messages=db.get_messages(session_id),
    )


async def test_a_label_naming_another_state_does_not_add_that_states_signals(db):
    session_id, message_id = _conversation(db)
    _tracking_row(db, session_id, message_id, expected_state="B")
    ai_service = _RecordingAiService()

    await _source(db, session_id, ai_service).get_turn_data(message_id, "A")

    assert ai_service.requested_signals == [{"alpha"}]


async def test_the_state_the_original_session_was_in_does_not_add_its_signals(db):
    session_id, message_id = _conversation(db)
    _tracking_row(db, session_id, message_id, new_state="B")
    ai_service = _RecordingAiService()

    await _source(db, session_id, ai_service).get_turn_data(message_id, "A")

    assert ai_service.requested_signals == [{"alpha"}]


async def test_the_values_returned_land_on_the_message(db):
    session_id, message_id = _conversation(db)
    ai_service = _RecordingAiService(signals={"alpha": 70})

    signal_values, _, _ = await _source(db, session_id, ai_service).get_turn_data(message_id, "A")

    assert signal_values == {"alpha": 70.0}
