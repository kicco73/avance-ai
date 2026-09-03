"""Integration tests for GET /api/projects/{project_name}/users/{username}/timeline,
exercising ChatService.get_timeline end to end: every real signal
snapshot and state transition for a user across their whole session
history, chronological — Manage Users' Timeline tab.
"""
from __future__ import annotations

import pytest

from session import Session

from conftest import parse_chat_turn_sse

pytestmark = pytest.mark.contract


def test_timeline_signals_span_every_session_chronologically(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with Session().impersonate("alice"):
        older = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 10}, older["id"])
        newer = client.post("/api/chat/sessions").json()
        app_db.save_signal_snapshot({"foo": 20}, newer["id"])

    response = client.get(f"/api/projects/{hello_project}/users/alice/timeline")

    assert response.status_code == 200
    body = response.json()
    assert [entry["values"] for entry in body["signals"]] == [{"foo": 10}, {"foo": 20}]
    assert body["signals"][0]["timestamp"] <= body["signals"][1]["timestamp"]


def test_timeline_excludes_signal_rows_but_still_includes_the_initial_state(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "bob")
    with Session().impersonate("bob"):
        session = client.get("/api/chat/session").json()
        client.get(f"/api/chat/sessions/{session['id']}/messages")
        turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
        client.put(
            f"/api/chat/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
        )

    response = client.get(f"/api/projects/{hello_project}/users/bob/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == []
    assert [t["new_state"] for t in body["transitions"]] == ["Hello"]


def test_timeline_includes_state_transitions(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with Session().impersonate("alice"):
        session = client.get("/api/chat/session").json()
        client.get(f"/api/chat/sessions/{session['id']}/messages")
        app_db.save_transition(None, "leave", "Goodbye", session["id"], "INFO")

    response = client.get(f"/api/projects/{hello_project}/users/alice/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == []
    assert [t["new_state"] for t in body["transitions"]] == ["Hello", "Goodbye"]


def test_timeline_is_scoped_to_the_given_user_and_project(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with Session().impersonate("alice"):
        alice_session = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 1}, alice_session["id"])

    app_db.set_active_project_id(hello_project, "carol")
    with Session().impersonate("carol"):
        carol_session = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 2}, carol_session["id"])

    response = client.get(f"/api/projects/{hello_project}/users/alice/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == [{"timestamp": body["signals"][0]["timestamp"], "values": {"foo": 1}}]
