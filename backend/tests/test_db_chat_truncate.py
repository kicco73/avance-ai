"""Db.truncate_session/latest_message_or_signal_timestamp — the
persistence half of "Restart from here". Every timestamp here is set
explicitly so the >= cutoff boundary can be tested exactly, rather than
racing real wall-clock time.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from db.models import Message, Tracking

pytestmark = pytest.mark.regression


def _make_session(db, *, username="user", project_name="proj", start, start_state="start"):
    db.ensure_project(project_name)
    db.publish_project(project_name)
    return db.create_chat_session(
        username=username,
        project_id=project_name,
        revision=db.get_project_published_revision(project_name),
        datetime_start=start,
        datetime_end=start,
        start_state=start_state,
        end_state=start_state,
    )


def _message_at(db, session_id, content, timestamp):
    message_id = db.save_message("user", content, session_id)
    Message.update(timestamp=timestamp).where(Message.id == message_id).execute()
    return message_id


def _signal_at(db, session_id, timestamp, *, old_state=None, new_state=None, message_id=None):
    if old_state is None and new_state is None:
        row_id = db.save_signal_snapshot({}, session_id, message_id=message_id)
    else:
        row_id = db.save_transition(
            old_state, "advance", new_state, session_id, transition_log_level="INFO", message_id=message_id
        )
    Tracking.update(timestamp=timestamp).where(Tracking.id == row_id).execute()
    return row_id


def test_truncating_deletes_every_message_and_signal_at_or_after_the_cutoff_rolling_the_state_back_with_them(db):
    """get_current_state always resolves to the newest surviving
    transition, so deleting the trailing Tracking rows is the entire
    rollback."""
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    kept_message = _message_at(db, session_id, "keep", datetime(2026, 1, 1, 10, 0, 0))
    _message_at(db, session_id, "cut (at cutoff)", datetime(2026, 1, 1, 10, 5, 0))
    _message_at(db, session_id, "cut (after)", datetime(2026, 1, 1, 10, 10, 0))
    kept_signal = _signal_at(db, session_id, datetime(2026, 1, 1, 10, 0, 0), old_state="start", new_state="middle")
    _signal_at(db, session_id, datetime(2026, 1, 1, 10, 10, 0), old_state="middle", new_state="end")
    assert db.get_current_state("proj") == "end"

    db.truncate_session(session_id, datetime(2026, 1, 1, 10, 5, 0))

    assert {m["id"] for m in db.get_messages(session_id)} == {kept_message}
    assert {row["id"] for row in db.get_signals(session_id)} == {kept_signal}
    assert db.get_current_state("proj") == "middle"


def test_truncating_never_deletes_the_projects_own_init_transition_row(db):
    """The one-time "" -> start_state bookkeeping row is scoped to the
    whole project, not this session — a cutoff before its timestamp must
    never remove it, or get_current_state falls back to "never initialized"."""
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    init_row = _signal_at(db, session_id, datetime(2026, 1, 1, 10, 0, 0), old_state="", new_state="start")

    db.truncate_session(session_id, datetime(2026, 1, 1, 9, 0, 0))  # before everything

    assert {row["id"] for row in db.get_signals(session_id)} == {init_row}


def test_latest_message_or_signal_timestamp_is_the_max_across_both_and_none_once_truncated_to_nothing(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    _message_at(db, session_id, "hi", datetime(2026, 1, 1, 10, 0, 0))
    _signal_at(db, session_id, datetime(2026, 1, 1, 10, 30, 0), old_state="start", new_state="middle")

    assert db.latest_message_or_signal_timestamp(session_id) == datetime(2026, 1, 1, 10, 30, 0)

    db.truncate_session(session_id, datetime(2026, 1, 1, 9, 0, 0))  # everything

    assert db.latest_message_or_signal_timestamp(session_id) is None
