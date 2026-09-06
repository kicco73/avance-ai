from __future__ import annotations

from datetime import datetime

import pytest


def _make_session(db, *, username="user", project_name="proj", start=datetime(2026, 1, 1, 10, 0, 0), start_state="start"):
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


@pytest.mark.regression
def test_snapshots_and_transitions_resolve_the_latest_values_and_current_state_independently(db):
    session_id = _make_session(db)

    db.save_signal_snapshot({"foo": 1}, session_id)
    assert db.get_latest_signal_snapshot("proj") == {"foo": 1}
    # A plain evaluation with no trigger firing must not look like a
    # transition to state-resolution queries.
    assert db.get_current_state("proj") is None

    db.save_transition("", "init", "start", session_id, transition_log_level="INFO")
    assert db.get_current_state("proj") == "start"
    # A manual/init transition carries no signal values — the latest
    # *values* snapshot is still the earlier evaluation.
    assert db.get_latest_signal_snapshot("proj") == {"foo": 1}

    db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO", signal_values={"foo": 42})
    assert db.get_current_state("proj") == "next"
    assert db.get_latest_signal_snapshot("proj") == {"foo": 42}

    db.reset_project("proj")
    assert db.get_current_state("proj") is None
    assert db.get_latest_signal_snapshot("proj") is None


@pytest.mark.regression
def test_current_state_is_scoped_by_project_and_self_loops_never_count_as_a_real_transition(db):
    session_a = _make_session(db, project_name="proj-a")
    session_b = _make_session(db, project_name="proj-b")

    db.save_transition("", "init", "start-a", session_a, transition_log_level="INFO")
    db.save_transition("start-b", "init", "start-b", session_b, transition_log_level="INFO")

    assert db.get_current_state("proj-a") == "start-a"
    assert db.get_current_state("proj-b") == "start-b"
    # A self-loop (old_state == new_state) doesn't count as a "real"
    # transition for history_cutoff purposes, but it is the current state.
    assert db.get_last_transition_timestamp("proj-b") is None


@pytest.mark.contract
def test_get_signals_returns_the_full_event_log_chronologically_with_every_annotation_field_present(db):
    session_id = _make_session(db)
    db.save_signal_snapshot({"foo": 1}, session_id)
    row_id = db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO", signal_values={"foo": 2})

    rows = db.get_signals(session_id)

    assert [r["values"] for r in rows] == ['{"foo": 1}', '{"foo": 2}']
    assert rows[0]["new_state"] is None
    assert rows[1]["old_state"] == "start"
    assert rows[1]["new_state"] == "next"
    assert rows[1]["id"] == row_id
    # Nothing writes these at creation — they're just always present.
    assert rows[0]["expected_values"] is None
    assert rows[0]["expected_state"] is None
    assert rows[0]["comment"] is None
    assert rows[0]["message_id"] is None


@pytest.mark.regression
def test_get_signals_is_scoped_to_its_own_session_and_empty_without_any(db):
    session_a = _make_session(db)
    session_b = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    session_c = _make_session(db, start=datetime(2026, 1, 3, 10, 0, 0))
    db.save_signal_snapshot({"foo": 1}, session_a)
    db.save_signal_snapshot({"foo": 2}, session_b)

    assert [r["values"] for r in db.get_signals(session_a)] == ['{"foo": 1}']
    assert [r["values"] for r in db.get_signals(session_b)] == ['{"foo": 2}']
    assert db.get_signals(session_c) == []


@pytest.mark.regression
def test_a_row_links_to_its_message_at_creation_or_afterwards_and_is_looked_up_both_ways(db):
    session_id = _make_session(db)
    unlinked_message = db.save_message("user", "hi", session_id)
    assert db.get_signal_row_by_message(unlinked_message) is None

    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id)
    db.link_signal_to_message(signal_row_id, unlinked_message)
    linked = db.get_signal_row_by_message(unlinked_message)
    assert linked is not None
    assert linked["id"] == signal_row_id
    assert db.get_signals(session_id)[0]["message_id"] == unlinked_message

    snapshot_message = db.save_message("assistant", "hi", session_id)
    snapshot_row = db.save_signal_snapshot({"foo": 1}, session_id, message_id=snapshot_message)
    assert db.get_signal_row_by_message(snapshot_message)["id"] == snapshot_row

    transition_message = db.save_message("assistant", "hi", session_id)
    transition_row = db.save_transition(
        "start", "advance", "next", session_id, transition_log_level="INFO", message_id=transition_message
    )
    assert db.get_signal_row_by_message(transition_message)["id"] == transition_row


@pytest.mark.regression
def test_tool_calls_are_readable_back_by_message_scoped_to_their_session_and_omitted_without_a_message(db):
    session_a = _make_session(db)
    session_b = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    message_a = db.save_message("assistant", "hi", session_a)
    message_b = db.save_message("assistant", "hi", session_b)
    calls = [{"name": "source_flights_select", "arguments": {"value": "paris"}, "result": "row"}]

    db.record_tool_calls(session_a, calls, message_id=message_a)
    db.record_tool_calls(session_a, [{"name": "orphan"}])
    db.record_tool_calls(session_b, [{"name": "b"}], message_id=message_b)

    assert db.get_tool_calls_by_message(session_a) == {message_a: calls}
    assert db.get_tool_calls_by_message(session_b) == {message_b: [{"name": "b"}]}


@pytest.mark.regression
def test_a_tool_calls_only_row_never_shows_up_in_get_signals_or_the_timeline(db):
    """A record_tool_calls row is its own kind, same as env/action_env —
    never mistaken for a signals row by get_signals' own event log."""
    session_id = _make_session(db)
    message_id = db.save_message("assistant", "hi", session_id)
    db.save_signal_snapshot({"foo": 1}, session_id)
    db.record_tool_calls(session_id, [{"name": "source_flights_select"}], message_id=message_id)

    rows = db.get_signals(session_id)
    assert len(rows) == 1
    assert rows[0]["values"] == '{"foo": 1}'

    timeline = db.get_timeline("proj", "user")
    assert timeline["signals"] == [{"timestamp": timeline["signals"][0]["timestamp"], "values": {"foo": 1}}]


@pytest.mark.regression
def test_comment_expected_values_and_expected_state_each_set_and_clear_and_read_back_by_message(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hi", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)

    db.set_signal_comment(signal_row_id, "Looks right to me.")
    assert db.get_signals(session_id)[0]["comment"] == "Looks right to me."
    assert db.get_signal_row_by_message(message_id)["comment"] == "Looks right to me."
    db.set_signal_comment(signal_row_id, None)
    assert db.get_signals(session_id)[0]["comment"] is None

    db.set_signal_expected_values(signal_row_id, {"foo": 75})
    assert db.get_signals(session_id)[0]["expected_values"] == '{"foo": 75}'
    db.set_signal_expected_values(signal_row_id, None)
    assert db.get_signals(session_id)[0]["expected_values"] is None

    db.set_signal_expected_state(signal_row_id, "b")
    assert db.get_signals(session_id)[0]["expected_state"] == "b"
    db.set_signal_expected_state(signal_row_id, None)
    assert db.get_signals(session_id)[0]["expected_state"] is None


@pytest.mark.regression
def test_clear_session_annotations_clears_only_its_own_sessions_annotations_keeping_every_real_observation(db):
    session_a = _make_session(db)
    session_b = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    row_a = db.save_signal_snapshot({"foo": 1}, session_a)
    row_b = db.save_transition("start", "advance", "next", session_a, transition_log_level="INFO")
    row_other = db.save_signal_snapshot({"foo": 1}, session_b)
    db.set_signal_expected_state(row_a, "start")
    db.set_signal_expected_values(row_a, {"foo": 75})
    db.set_signal_expected_state(row_b, "next")
    db.set_signal_expected_state(row_other, "start")

    db.clear_session_annotations(session_a)

    rows = {r["id"]: r for r in db.get_signals(session_a)}
    assert set(rows) == {row_a, row_b}
    assert rows[row_a]["expected_state"] is None
    assert rows[row_a]["expected_values"] is None
    assert rows[row_b]["expected_state"] is None
    assert rows[row_b]["new_state"] == "next"
    assert db.get_signals(session_b)[0]["expected_state"] == "start"


@pytest.mark.regression
def test_clearing_annotations_deletes_an_emptied_session_start_row_and_delete_signal_row_removes_any_row(db):
    """A session-start bookkeeping row (old_state == "") only ever exists
    to hold an annotation — clearing it must remove the row entirely."""
    session_id = _make_session(db)
    start_row = db.save_transition("", "", "start", session_id, transition_log_level="INFO")
    db.set_signal_expected_state(start_row, "start")

    db.clear_session_annotations(session_id)
    assert db.get_signals(session_id) == []

    row_id = db.save_signal_snapshot({"foo": 1}, session_id)
    db.delete_signal_row(row_id)
    assert db.get_signals(session_id) == []


@pytest.mark.contract
def test_origin_is_validated_on_write_and_persisted(db):
    session_id = _make_session(db)

    with pytest.raises(ValueError):
        db.save_transition("", "init", "start", session_id, transition_log_level="INFO", origin="not-a-real-origin")
    with pytest.raises(ValueError):
        db.import_tracking_row(
            session_id, old_state="", action="init", new_state="start", values=None,
            expected_state=None, expected_values=None, comment=None, message_id=None,
            timestamp=None, origin="not-a-real-origin",
        )

    db.save_transition("start", "advance", "next", session_id, transition_log_level="INFO", origin="trigger")
    assert db.get_signals(session_id)[0]["origin"] == "trigger"


_ORIGIN_PROJECT_YAML = """
project:
  id: origin_proj
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: advance
        ui-label: Advance
        ui-button: Advance
        target: b
  b:
    ui-label: B
    contextual-prompt: bye
"""


@pytest.mark.contract
def test_session_bootstrap_records_origin_init_action_and_a_manual_action_origin_manual(client, app_db):
    app_db.ensure_project("origin-proj")
    app_db.save_project_files(
        "origin-proj", {"index.yml": _ORIGIN_PROJECT_YAML.encode("utf-8")}, {"index.yml": "text/yaml"},
    )
    app_db.publish_project("origin-proj")
    app_db.set_active_project_id("origin-proj", "user")
    session = client.get("/api/chat/session").json()

    # Opening a session's own first message is what triggers ChatService's
    # bootstrap (_ensure_project_bootstrap), not the session lookup itself.
    client.get(f"/api/chat/sessions/{session['id']}/messages")
    response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "advance"})
    assert response.status_code == 200, response.text

    signals = app_db.get_signals(session["id"])
    init_rows = [row for row in signals if row["old_state"] == ""]
    assert len(init_rows) == 1
    assert init_rows[0]["origin"] == "init-action"
    manual_rows = [row for row in signals if row["action"] == "advance"]
    assert len(manual_rows) == 1
    assert manual_rows[0]["origin"] == "manual"
