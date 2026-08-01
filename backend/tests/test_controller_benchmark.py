"""Integration tests for the backend pieces the "Benchmark project" view
relies on: the session-scoped Signals timeline endpoint, and point-in-time
(message_id-scoped) metrics — see chat/chat_service.py's
get_session_signals/get_metrics.
"""
from __future__ import annotations


def test_get_session_signals_returns_the_full_event_log(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.get(f"/api/chat/sessions/{session['id']}/signals")

    assert response.status_code == 200
    assert response.json() == []  # "Hello world" declares no signals/triggers


def test_get_session_signals_is_404_for_someone_elses_or_unknown_session(client, hello_project):
    response = client.get("/api/chat/sessions/999999/signals")
    assert response.status_code == 404


def test_get_metrics_without_message_id_is_the_live_current_history(client, hello_project):
    session = client.get("/api/chat/session").json()
    for text in ("hi", "again"):
        client.post("/api/chat/messages", json={"message": text, "session_id": session["id"]})

    live = {m["name"]: m["value"] for m in client.get("/api/chat/metrics").json()}

    assert live["engagement"] > 0.0


def test_get_metrics_with_message_id_restricts_to_that_points_history(client, hello_project):
    session = client.get("/api/chat/session").json()
    first_turn = client.post(
        "/api/chat/messages", json={"message": "first", "session_id": session["id"]}
    ).json()
    first_message_id = first_turn["reply"][0]["id"]

    # A second turn happens afterward — engagement should have grown since,
    # but a lookup pinned to the first message's own timestamp must not
    # reflect it.
    client.post("/api/chat/messages", json={"message": "second", "session_id": session["id"]})

    at_first = {m["name"]: m["value"] for m in client.get(f"/api/chat/metrics?message_id={first_message_id}").json()}
    live = {m["name"]: m["value"] for m in client.get("/api/chat/metrics").json()}

    assert at_first["engagement"] <= live["engagement"]


def test_get_metrics_response_shape_is_unchanged_by_message_id(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["reply"][0]["id"]

    response = client.get(f"/api/chat/metrics?message_id={message_id}")

    assert response.status_code == 200
    body = response.json()
    assert {m["name"] for m in body} == {
        "engagement", "retention", "activity_consistency", "state_stability", "signal_stability"
    }
    for metric in body:
        assert set(metric) == {"name", "ui_label", "ui_description", "value"}


def test_get_metrics_with_an_unknown_message_id_is_404(client, hello_project):
    client.get("/api/chat/session")
    response = client.get("/api/chat/metrics?message_id=999999")
    assert response.status_code == 404


def test_get_messages_includes_is_evaluation_point(client, hello_project):
    """"Hello world" declares no signals/triggers (see
    test_get_session_signals_returns_the_full_event_log) — nothing it
    sends is ever an evaluation point."""
    session = client.get("/api/chat/session").json()
    client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]})

    rows = client.get(f"/api/chat/messages?session_id={session['id']}").json()

    assert rows
    assert all(row["is_evaluation_point"] is False for row in rows)


def test_put_expected_state_is_409_for_a_non_evaluation_point_message(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["reply"][0]["id"]

    response = client.put(f"/api/chat/messages/{message_id}/expected-state", json={"expected_state": "start"})

    assert response.status_code == 409


def test_put_expected_signals_is_409_for_a_non_evaluation_point_message(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["reply"][0]["id"]

    response = client.put(
        f"/api/chat/messages/{message_id}/expected-signals", json={"expected_values": {"foo": 50}}
    )

    assert response.status_code == 409


def test_put_expected_state_is_404_for_an_unknown_message(client, hello_project):
    response = client.put("/api/chat/messages/999999/expected-state", json={"expected_state": "start"})
    assert response.status_code == 404


def test_put_expected_signals_is_404_for_an_unknown_message(client, hello_project):
    response = client.put("/api/chat/messages/999999/expected-signals", json={"expected_values": {"foo": 50}})
    assert response.status_code == 404
