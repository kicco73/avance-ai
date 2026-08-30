"""Integration tests for the backend pieces the "Label sessions" view
relies on: the session-scoped Tracking timeline endpoint, and point-in-time
(message_id-scoped) metrics — see chat/chat_service.py's
get_session_signals/get_metrics.
"""
from __future__ import annotations

import pytest

from conftest import parse_chat_turn_sse


@pytest.mark.contract
def test_get_session_signals_returns_the_full_event_log(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.get(f"/api/chat/sessions/{session['id']}/signals")

    assert response.status_code == 200
    assert response.json() == []  # "Hello world" declares no signals/triggers


@pytest.mark.contract
def test_get_session_signals_is_404_for_someone_elses_or_unknown_session(client, hello_project):
    response = client.get("/api/chat/sessions/999999/signals")
    assert response.status_code == 404


@pytest.mark.contract
def test_get_metrics_without_message_id_is_the_live_current_history(client, hello_project):
    session = client.get("/api/chat/session").json()
    for text in ("hi", "again"):
        client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": text})

    live = {m["name"]: m["value"] for m in client.get("/api/projects/hello/metrics").json()}

    assert live["engagement"] > 0.0


@pytest.mark.contract
def test_get_metrics_with_message_id_restricts_to_that_points_history(client, hello_project):
    session = client.get("/api/chat/session").json()
    first_turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "first"}))
    first_message_id = first_turn["assistant_message_id"]

    # engagement should have grown since, but a lookup pinned to the
    # first message's timestamp must not reflect it.
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "second"})

    at_first = {m["name"]: m["value"] for m in client.get(f"/api/projects/hello/metrics?message_id={first_message_id}").json()}
    live = {m["name"]: m["value"] for m in client.get("/api/projects/hello/metrics").json()}

    assert at_first["engagement"] <= live["engagement"]


@pytest.mark.contract
def test_get_metrics_response_shape_is_unchanged_by_message_id(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
    message_id = turn["assistant_message_id"]

    response = client.get(f"/api/projects/hello/metrics?message_id={message_id}")

    assert response.status_code == 200
    body = response.json()
    # Always a one_session context — retention/activity_consistency are
    # excluded from that scope.
    assert {m["name"] for m in body} == {"engagement", "state_stability", "signal_stability"}
    for metric in body:
        assert set(metric) == {"name", "ui_label", "ui_description", "value"}


@pytest.mark.contract
def test_get_metrics_with_an_unknown_message_id_is_404(client, hello_project):
    client.get("/api/chat/session")
    response = client.get("/api/projects/hello/metrics?message_id=999999")
    assert response.status_code == 404


@pytest.mark.contract
def test_get_messages_response_shape_has_no_annotation_fields(client, hello_project):
    """Annotation-related fields and the evaluation-point link live on
    Tracking (see get_session_signals), not on the message row."""
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    rows = client.get(f"/api/chat/sessions/{session['id']}/messages").json()

    assert rows
    for row in rows:
        assert set(row) == {"id", "role", "content", "audio_text", "reaction", "tokens", "timestamp", "session_id"}


@pytest.mark.contract
def test_put_expected_state_is_409_for_a_non_evaluation_point_message(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
    message_id = turn["assistant_message_id"]

    response = client.put(f"/api/chat/messages/{message_id}/expected-state", json={"expected_state": "start"})

    assert response.status_code == 409


@pytest.mark.contract
def test_put_expected_signals_is_409_for_a_non_evaluation_point_message(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
    message_id = turn["assistant_message_id"]

    response = client.put(
        f"/api/chat/messages/{message_id}/expected-signals", json={"expected_values": {"foo": 50}}
    )

    assert response.status_code == 409


@pytest.mark.contract
def test_put_expected_state_is_404_for_an_unknown_message(client, hello_project):
    response = client.put("/api/chat/messages/999999/expected-state", json={"expected_state": "start"})
    assert response.status_code == 404


@pytest.mark.contract
def test_put_expected_signals_is_404_for_an_unknown_message(client, hello_project):
    response = client.put("/api/chat/messages/999999/expected-signals", json={"expected_values": {"foo": 50}})
    assert response.status_code == 404


@pytest.mark.contract
def test_get_test_metrics_response_shape(client, hello_project, app_db):
    response = client.get("/api/projects/hello/tests/metrics")

    assert response.status_code == 200
    body = response.json()
    # This is the frontend's own metric catalog (see MetricService.
    # get_benchmark_metrics) — every metric belongs here, including
    # benchmark_stability/benchmark_consistency (scoped to {all_sessions}
    # in a real run's own results, but that scoping is irrelevant to a
    # name -> label/description lookup).
    assert {m["name"] for m in body} == {
        "state_accuracy", "state_accuracy_stable", "state_accuracy_transition",
        "signal_accuracy", "transition_responsiveness", "benchmark_accuracy",
        "benchmark_stability", "benchmark_consistency",
    }
    for metric in body:
        assert set(metric) == {"name", "ui_label", "ui_description", "value", "sample_count"}


@pytest.mark.regression
def test_get_test_metrics_reflects_annotations(client, hello_project, app_db):
    """"Hello world" declares no triggers, so there's no real chat-turn
    path to a linked Tracking row — written directly via app_db."""
    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
    message_id = turn["assistant_message_id"]
    signal_row_id = app_db.save_signal_snapshot({"foo": 80}, session["id"], message_id=message_id)

    before = {m["name"]: m for m in client.get("/api/projects/hello/tests/metrics").json()}
    assert before["state_accuracy"]["sample_count"] == 0

    app_db.set_signal_expected_state(signal_row_id, "Hello")  # hello_project's own init_action.target

    after = {m["name"]: m for m in client.get("/api/projects/hello/tests/metrics").json()}
    assert after["state_accuracy"]["sample_count"] == 1
    assert after["state_accuracy"]["value"] == 100.0


@pytest.mark.contract
def test_get_test_metrics_can_be_scoped_to_one_session(client, hello_project):
    session = client.get("/api/chat/session").json()
    response = client.get(f"/api/projects/hello/tests/metrics?session_id={session['id']}")
    assert response.status_code == 200


@pytest.mark.contract
def test_get_test_metrics_is_404_for_someone_elses_or_unknown_session(client, hello_project):
    response = client.get("/api/projects/hello/tests/metrics?session_id=999999")
    assert response.status_code == 404


@pytest.mark.contract
def test_delete_session_annotations_clears_everything_in_that_session(client, hello_project, app_db):
    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
    message_id = turn["assistant_message_id"]
    signal_row_id = app_db.save_signal_snapshot({"foo": 80}, session["id"], message_id=message_id)
    app_db.set_signal_expected_state(signal_row_id, "Hello")
    app_db.set_signal_expected_values(signal_row_id, {"foo": 80})

    response = client.delete(f"/api/chat/sessions/{session['id']}/annotations")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    row = app_db.get_signal_row_by_message(message_id)
    assert row["expected_state"] is None
    assert row["expected_values"] is None
    # The actually-observed values must stay untouched.
    assert row["values"] is not None


@pytest.mark.contract
def test_delete_session_annotations_is_404_for_someone_elses_or_unknown_session(client, hello_project):
    response = client.delete("/api/chat/sessions/999999/annotations")
    assert response.status_code == 404


@pytest.mark.regression
def test_annotating_a_later_sessions_own_start_materializes_a_signals_row(client, hello_project):
    """Only the first session ever opened for a project gets a real
    "" -> start_state Tracking row — a later session has nothing real to
    annotate against until an expert actually tries."""
    first = client.get("/api/chat/session").json()
    client.get(f"/api/chat/sessions/{first['id']}/messages")

    second = client.post("/api/chat/sessions").json()
    messages = client.get(f"/api/chat/sessions/{second['id']}/messages").json()
    assert messages
    first_message_id = messages[0]["id"]
    assert client.get(f"/api/chat/sessions/{second['id']}/signals").json() == []

    response = client.put(
        f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": "Hello"}
    )

    assert response.status_code == 200
    signals = client.get(f"/api/chat/sessions/{second['id']}/signals").json()
    assert len(signals) == 1
    assert signals[0]["old_state"] == ""
    assert signals[0]["message_id"] == first_message_id
    assert signals[0]["expected_state"] == "Hello"


@pytest.mark.regression
def test_clearing_the_only_annotation_on_a_materialized_start_row_deletes_it(client, hello_project):
    first = client.get("/api/chat/session").json()
    client.get(f"/api/chat/sessions/{first['id']}/messages")
    second = client.post("/api/chat/sessions").json()
    first_message_id = client.get(f"/api/chat/sessions/{second['id']}/messages").json()[0]["id"]
    client.put(f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": "Hello"})

    response = client.put(
        f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": None}
    )

    assert response.status_code == 200
    assert response.json() is None
    assert client.get(f"/api/chat/sessions/{second['id']}/signals").json() == []


@pytest.mark.regression
def test_annotating_the_first_sessions_own_start_materializes_a_signals_row(client, hello_project):
    """The automaton's first ("" -> start_state) transition fires before
    any message exists, so it starts unlinked — a domain expert can
    still annotate it via the same lazy-link path a later session uses."""
    session = client.get("/api/chat/session").json()
    messages = client.get(f"/api/chat/sessions/{session['id']}/messages").json()  # triggers open_if_needed
    assert messages
    first_message_id = messages[0]["id"]

    signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    init_row = next(row for row in signals if row["old_state"] == "")
    assert init_row["message_id"] is None

    response = client.put(
        f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": "Hello"}
    )

    assert response.status_code == 200
    signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    init_row = next(row for row in signals if row["old_state"] == "")
    assert init_row["message_id"] == first_message_id
    assert init_row["expected_state"] == "Hello"
