"""Integration tests for the backend pieces the "Label sessions" view
relies on: the session-scoped Tracking timeline endpoint, and point-in-time
(message_id-scoped) metrics — see chat/chat_service.py's
get_session_signals/get_metrics.
"""
from __future__ import annotations

import pytest

from conftest import chat_turn


def _metrics(client, project_id, query: str = "") -> dict:
    return {m["name"]: m["value"] for m in client.get(f"/api/projects/{project_id}/metrics{query}").json()}


def _first_message_of_a_second_session(client) -> tuple[int, int]:
    first = client.get("/api/chat/session").json()
    client.get(f"/api/chat/sessions/{first['id']}/messages")
    second = client.post("/api/chat/sessions").json()
    messages = client.get(f"/api/chat/sessions/{second['id']}/messages").json()
    assert messages
    return second["id"], messages[0]["id"]


@pytest.mark.contract
def test_get_session_signals_returns_the_full_event_log_and_404s_for_an_unknown_session(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.get(f"/api/chat/sessions/{session['id']}/signals")
    assert response.status_code == 200
    assert response.json() == []  # "Hello world" declares no signals/triggers

    assert client.get("/api/chat/sessions/999999/signals").status_code == 404


@pytest.mark.contract
def test_get_metrics_is_the_live_history_unless_pinned_to_a_message_keeping_its_shape_and_404ing_an_unknown_one(client, hello_project):
    session = client.get("/api/chat/session").json()
    first_message_id = chat_turn(client, session['id'], "first")["assistant_message_id"]
    chat_turn(client, session['id'], "second")

    live = _metrics(client, hello_project)
    assert live["engagement"] > 0.0
    # engagement should have grown since, but a lookup pinned to the
    # first message's timestamp must not reflect it.
    assert _metrics(client, hello_project, f"?message_id={first_message_id}")["engagement"] <= live["engagement"]

    response = client.get(f"/api/projects/{hello_project}/metrics?message_id={first_message_id}")
    assert response.status_code == 200
    body = response.json()
    # Always a one_session context — retention/activity_consistency are
    # excluded from that scope.
    assert {m["name"] for m in body} == {"engagement", "state_stability", "signal_stability"}
    for metric in body:
        assert set(metric) == {"name", "ui_label", "ui_description", "value"}

    assert client.get(f"/api/projects/{hello_project}/metrics?message_id=999999").status_code == 404


@pytest.mark.contract
def test_get_messages_response_shape_has_no_annotation_fields(client, hello_project):
    """Annotation-related fields and the evaluation-point link live on
    Tracking (see get_session_signals), not on the message row."""
    session = client.get("/api/chat/session").json()
    chat_turn(client, session['id'], "hi")
    rows = client.get(f"/api/chat/sessions/{session['id']}/messages").json()

    assert rows
    for row in rows:
        assert set(row) == {
            "id", "role", "content", "audio_text", "reaction", "tokens", "cache_read_tokens", "timestamp", "session_id",
        }


@pytest.mark.contract
def test_put_expected_state_and_signals_are_409_for_a_non_evaluation_point_message_and_404_for_an_unknown_one(client, hello_project):
    session = client.get("/api/chat/session").json()
    message_id = chat_turn(client, session['id'], "hi")["assistant_message_id"]

    assert client.put(f"/api/chat/messages/{message_id}/expected-state", json={"expected_state": "start"}).status_code == 409
    assert client.put(f"/api/chat/messages/{message_id}/expected-signals", json={"expected_values": {"foo": 50}}).status_code == 409
    assert client.put("/api/chat/messages/999999/expected-state", json={"expected_state": "start"}).status_code == 404
    assert client.put("/api/chat/messages/999999/expected-signals", json={"expected_values": {"foo": 50}}).status_code == 404


@pytest.mark.contract
def test_get_test_metrics_lists_the_whole_catalog_optionally_scoped_to_a_session_that_must_exist(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/tests/metrics")

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

    session = client.get("/api/chat/session").json()
    assert client.get(f"/api/projects/{hello_project}/tests/metrics?session_id={session['id']}").status_code == 200
    assert client.get(f"/api/projects/{hello_project}/tests/metrics?session_id=999999").status_code == 404


@pytest.mark.regression
def test_get_test_metrics_reflects_annotations_and_deleting_them_clears_only_the_annotations(client, hello_project, app_db):
    """"Hello world" declares no triggers, so there's no real chat-turn
    path to a linked Tracking row — written directly via app_db."""
    session = client.get("/api/chat/session").json()
    message_id = chat_turn(client, session['id'], "hi")["assistant_message_id"]
    signal_row_id = app_db.save_signal_snapshot({"foo": 80}, session["id"], message_id=message_id)

    before = {m["name"]: m for m in client.get(f"/api/projects/{hello_project}/tests/metrics").json()}
    assert before["state_accuracy"]["sample_count"] == 0

    app_db.set_signal_expected_state(signal_row_id, "Hello")  # hello_project's own init_action.target
    app_db.set_signal_expected_values(signal_row_id, {"foo": 80})

    after = {m["name"]: m for m in client.get(f"/api/projects/{hello_project}/tests/metrics").json()}
    assert after["state_accuracy"]["sample_count"] == 1
    assert after["state_accuracy"]["value"] == 100.0

    response = client.delete(f"/api/chat/sessions/{session['id']}/annotations")
    assert response.status_code == 200
    assert response.json() == {"success": True}
    row = app_db.get_signal_row_by_message(message_id)
    assert row["expected_state"] is None
    assert row["expected_values"] is None
    assert row["values"] is not None

    assert client.delete("/api/chat/sessions/999999/annotations").status_code == 404


@pytest.mark.regression
def test_annotating_a_later_sessions_own_start_materializes_a_signals_row_that_clearing_the_annotation_deletes(client, hello_project):
    """Only the first session ever opened for a project gets a real
    "" -> start_state Tracking row — a later session has nothing real to
    annotate against until an expert actually tries."""
    session_id, first_message_id = _first_message_of_a_second_session(client)
    assert client.get(f"/api/chat/sessions/{session_id}/signals").json() == []

    response = client.put(f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": "Hello"})

    assert response.status_code == 200
    signals = client.get(f"/api/chat/sessions/{session_id}/signals").json()
    assert len(signals) == 1
    assert signals[0]["old_state"] == ""
    assert signals[0]["message_id"] == first_message_id
    assert signals[0]["expected_state"] == "Hello"

    response = client.put(f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": None})

    assert response.status_code == 200
    assert response.json() is None
    assert client.get(f"/api/chat/sessions/{session_id}/signals").json() == []


@pytest.mark.regression
def test_annotating_the_first_sessions_own_start_links_the_unlinked_init_row_to_the_first_message(client, hello_project):
    """The automaton's first ("" -> start_state) transition fires before
    any message exists, so it starts unlinked — a domain expert can
    still annotate it via the same lazy-link path a later session uses."""
    session = client.get("/api/chat/session").json()
    messages = client.get(f"/api/chat/sessions/{session['id']}/messages").json()
    assert messages
    first_message_id = messages[0]["id"]

    signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    init_row = next(row for row in signals if row["old_state"] == "")
    assert init_row["message_id"] is None

    response = client.put(f"/api/chat/messages/{first_message_id}/expected-state", json={"expected_state": "Hello"})

    assert response.status_code == 200
    signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    init_row = next(row for row in signals if row["old_state"] == "")
    assert init_row["message_id"] == first_message_id
    assert init_row["expected_state"] == "Hello"
