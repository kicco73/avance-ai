from __future__ import annotations

from datetime import datetime


def _make_session(db, *, username="user", project_name="proj", start, start_state="start"):
    return db.create_chat_session(
        username=username,
        project_name=project_name,
        datetime_start=start,
        datetime_end=start,
        start_state=start_state,
        end_state=start_state,
    )


def test_save_signal_snapshot_without_a_transition(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_signal_snapshot({"foo": 1}, session_id)

    assert db.get_latest_signal_snapshot("proj") == {"foo": 1}
    # A plain evaluation with no trigger firing must not look like a
    # transition to state-resolution queries.
    assert db.get_current_state("proj") is None


def test_save_transition_without_signal_values(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_transition("", "init", "start", session_id, transition_log_level="INFO")

    assert db.get_current_state("proj") == "start"
    # A manual/init transition carries no signal values.
    assert db.get_latest_signal_snapshot("proj") is None


def test_save_transition_with_signal_values_is_visible_as_latest_snapshot(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_transition(
        "start", "advance", "next", session_id, transition_log_level="INFO", signal_values={"foo": 42}
    )

    assert db.get_current_state("proj") == "next"
    assert db.get_latest_signal_snapshot("proj") == {"foo": 42}


def test_get_latest_signal_snapshot_ignores_transition_only_rows(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_signal_snapshot({"foo": 1}, session_id)
    db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO")

    # The latest row overall is the transition (no values) — the latest
    # *values* snapshot is still the earlier evaluation.
    assert db.get_latest_signal_snapshot("proj") == {"foo": 1}


def test_get_current_state_scoped_by_project_via_session_join(db):
    session_a = _make_session(db, project_name="proj-a", start=datetime(2026, 1, 1, 10, 0, 0))
    session_b = _make_session(db, project_name="proj-b", start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_transition("", "init", "start-a", session_a, transition_log_level="INFO")
    db.save_transition("", "init", "start-b", session_b, transition_log_level="INFO")

    assert db.get_current_state("proj-a") == "start-a"
    assert db.get_current_state("proj-b") == "start-b"


def test_get_last_transition_timestamp_excludes_self_loops(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_transition("start", "init", "start", session_id, transition_log_level="INFO")

    # A self-loop (old_state == new_state) doesn't count as a "real"
    # transition for history_cutoff purposes.
    assert db.get_last_transition_timestamp("proj") is None
    # ...but it does count as the current state.
    assert db.get_current_state("proj") == "start"


def test_reset_project_deletes_signals_rows_too(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_transition("", "init", "start", session_id, transition_log_level="INFO", signal_values={"foo": 1})

    db.reset_project("proj")

    assert db.get_current_state("proj") is None
    assert db.get_latest_signal_snapshot("proj") is None
