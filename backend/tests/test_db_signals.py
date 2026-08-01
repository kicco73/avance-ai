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


def test_get_signals_returns_the_full_event_log_chronologically(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_signal_snapshot({"foo": 1}, session_id)
    db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO", signal_values={"foo": 2})

    rows = db.get_signals(session_id)

    assert [r["values"] for r in rows] == ['{"foo": 1}', '{"foo": 2}']
    assert rows[0]["new_state"] is None  # a plain snapshot, no transition
    assert rows[1]["old_state"] == "start"
    assert rows[1]["new_state"] == "next"


def test_get_signals_is_scoped_to_its_own_session(db):
    session_a = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    session_b = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    db.save_signal_snapshot({"foo": 1}, session_a)
    db.save_signal_snapshot({"foo": 2}, session_b)

    assert [r["values"] for r in db.get_signals(session_a)] == ['{"foo": 1}']
    assert [r["values"] for r in db.get_signals(session_b)] == ['{"foo": 2}']


def test_get_signals_on_a_session_with_no_signals_is_empty(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    assert db.get_signals(session_id) == []


def test_get_signals_includes_expected_values_field(db):
    """Nothing writes expected_values yet (see Signals.expected_values'
    own docstring) — it's just always present, currently always None."""
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_signal_snapshot({"foo": 1}, session_id)

    rows = db.get_signals(session_id)

    assert rows[0]["expected_values"] is None


def test_get_signals_includes_message_id_field(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id)

    rows = db.get_signals(session_id)
    assert rows[0]["message_id"] is None

    db.link_signal_to_message(signal_row_id, message_id)
    rows = db.get_signals(session_id)
    assert rows[0]["message_id"] == message_id


def test_get_signals_includes_expected_state_field(db):
    """Nothing writes expected_state yet in this test (see
    Signals.expected_state's own docstring) — it's just always present,
    currently always None."""
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_signal_snapshot({"foo": 1}, session_id)

    rows = db.get_signals(session_id)

    assert rows[0]["expected_state"] is None


def test_set_signal_expected_values_sets_and_clears(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id)

    db.set_signal_expected_values(signal_row_id, {"foo": 75})
    assert db.get_signals(session_id)[0]["expected_values"] == '{"foo": 75}'

    db.set_signal_expected_values(signal_row_id, None)
    assert db.get_signals(session_id)[0]["expected_values"] is None


def test_set_signal_expected_state_sets_and_clears(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id)

    db.set_signal_expected_state(signal_row_id, "b")
    assert db.get_signals(session_id)[0]["expected_state"] == "b"

    db.set_signal_expected_state(signal_row_id, None)
    assert db.get_signals(session_id)[0]["expected_state"] is None


def test_link_signal_to_message_sets_the_fk_both_ways(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id)

    db.link_signal_to_message(signal_row_id, message_id)

    linked = db.get_signal_row_by_message(message_id)
    assert linked is not None
    assert linked["id"] == signal_row_id
    assert db.get_signals(session_id)[0]["message_id"] == message_id


def test_get_signal_row_by_message_is_none_when_unlinked(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    assert db.get_signal_row_by_message(message_id) is None


def test_save_signal_snapshot_accepts_a_message_id_at_creation(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("assistant", "hi", session_id)

    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)

    linked = db.get_signal_row_by_message(message_id)
    assert linked is not None
    assert linked["id"] == signal_row_id


def test_save_transition_accepts_a_message_id_at_creation(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("assistant", "hi", session_id)

    row_id = db.save_transition(
        "start", "advance", "next", session_id, transition_log_level="INFO", message_id=message_id
    )

    linked = db.get_signal_row_by_message(message_id)
    assert linked is not None
    assert linked["id"] == row_id


def test_save_transition_returns_the_new_row_id(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    row_id = db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO")
    assert db.get_signals(session_id)[0]["id"] == row_id


def test_reset_project_deletes_signals_rows_too(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_transition("", "init", "start", session_id, transition_log_level="INFO", signal_values={"foo": 1})

    db.reset_project("proj")

    assert db.get_current_state("proj") is None
    assert db.get_latest_signal_snapshot("proj") is None


def test_clear_session_annotations_clears_expected_state_and_values(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    row_a = db.save_signal_snapshot({"foo": 1}, session_id)
    row_b = db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO")
    db.set_signal_expected_state(row_a, "start")
    db.set_signal_expected_values(row_a, {"foo": 75})
    db.set_signal_expected_state(row_b, "next")

    db.clear_session_annotations(session_id)

    rows = {r["id"]: r for r in db.get_signals(session_id)}
    assert rows[row_a]["expected_state"] is None
    assert rows[row_a]["expected_values"] is None
    assert rows[row_b]["expected_state"] is None
    # The actual observation itself must never be touched.
    assert rows[row_b]["new_state"] == "next"


def test_clear_session_annotations_is_scoped_to_its_own_session(db):
    session_a = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    session_b = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    row_a = db.save_signal_snapshot({"foo": 1}, session_a)
    row_b = db.save_signal_snapshot({"foo": 1}, session_b)
    db.set_signal_expected_state(row_a, "start")
    db.set_signal_expected_state(row_b, "start")

    db.clear_session_annotations(session_a)

    assert db.get_signals(session_a)[0]["expected_state"] is None
    assert db.get_signals(session_b)[0]["expected_state"] == "start"
