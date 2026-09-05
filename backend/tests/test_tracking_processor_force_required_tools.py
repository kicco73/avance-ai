"""TrackingProcessor.force_required_tools_for — whether ai-must-query-sources
must be forced this turn: only on the first turn generated since its own
state was last entered (a real transition, a self-loop action re-entering
the same state, or the project's own bootstrap), decided from Tracking/
Message history alone, never from anything the model said.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from automaton.automaton import State
from tracking.tracking_processor import TrackingProcessor, UserVariables

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"
T0 = datetime(2026, 1, 1, 12, 0, 0)


def _session(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session("user", PROJECT_ID, db.get_project_revision(PROJECT_ID))


def _processor(db, session_id: int, state: State) -> TrackingProcessor:
    user = UserVariables(automaton=object(), state=state, project_id=PROJECT_ID, session_id=session_id)
    processor = TrackingProcessor.__new__(TrackingProcessor)
    processor.db = db
    processor.user = user
    return processor


def _enter_state(db, session_id: int, old_state: str, new_state: str, at: datetime) -> None:
    db.import_tracking_row(
        session_id, old_state=old_state, action="go", new_state=new_state,
        values=None, expected_state=None, expected_values=None, comment=None,
        message_id=None, timestamp=at,
    )


def _assistant_message(db, session_id: int, at: datetime) -> None:
    db.save_message("assistant", "hi", session_id, timestamp=at)


def test_a_state_with_no_ai_must_query_sources_is_never_forced(db):
    session_id = _session(db)
    state = State(key="a", ui_label="A", final=False, contextual_prompt="hi")

    assert _processor(db, session_id, state).force_required_tools_for(state) is False


def test_the_first_turn_after_entering_the_state_is_forced(db):
    session_id = _session(db)
    state = State(key="a", ui_label="A", final=False, contextual_prompt="hi", ai_must_query_sources=("flights",))
    _enter_state(db, session_id, "", "a", T0)

    assert _processor(db, session_id, state).force_required_tools_for(state) is True


def test_no_transition_row_at_all_yet_is_still_forced(db):
    """Before the project's own bootstrap transition has been recorded —
    force_required_tools_for treats "never entered" the same as "just
    entered", not as "already answered"."""
    session_id = _session(db)
    state = State(key="a", ui_label="A", final=False, contextual_prompt="hi", ai_must_query_sources=("flights",))

    assert _processor(db, session_id, state).force_required_tools_for(state) is True


def test_a_second_turn_in_the_same_state_is_no_longer_forced(db):
    session_id = _session(db)
    state = State(key="a", ui_label="A", final=False, contextual_prompt="hi", ai_must_query_sources=("flights",))
    _enter_state(db, session_id, "", "a", T0)
    _assistant_message(db, session_id, T0 + timedelta(seconds=1))

    assert _processor(db, session_id, state).force_required_tools_for(state) is False


def test_a_self_loop_re_entering_the_state_forces_it_again(db):
    session_id = _session(db)
    state = State(key="a", ui_label="A", final=False, contextual_prompt="hi", ai_must_query_sources=("flights",))
    _enter_state(db, session_id, "", "a", T0)
    _assistant_message(db, session_id, T0 + timedelta(seconds=1))
    # A self-loop action lands back on "a" — old_state == new_state == "a".
    _enter_state(db, session_id, "a", "a", T0 + timedelta(seconds=2))

    assert _processor(db, session_id, state).force_required_tools_for(state) is True


def test_an_assistant_message_from_a_different_state_does_not_count(db):
    """An assistant message answered while the session was in a *different*
    state (before the transition landing on this one) must never satisfy
    "already answered here" — only compared against the message table via
    the timestamp of *this* state's own most recent entry."""
    session_id = _session(db)
    state = State(key="a", ui_label="A", final=False, contextual_prompt="hi", ai_must_query_sources=("flights",))
    _assistant_message(db, session_id, T0)
    _enter_state(db, session_id, "start", "a", T0 + timedelta(seconds=1))

    assert _processor(db, session_id, state).force_required_tools_for(state) is True
