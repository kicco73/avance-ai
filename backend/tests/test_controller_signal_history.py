"""Integration tests for GET /api/projects/{project_name}/users/{username}/signal-history,
exercising ChatService.get_signal_history end to end: every real signal
snapshot for a user across their whole session history, chronological —
Manage Users' Trends tab.
"""
from __future__ import annotations

import pytest

from session import Session

pytestmark = pytest.mark.contract


def test_signal_history_spans_every_session_chronologically(client, app_db, hello_project):
    app_db.set_active_project_name(hello_project, "alice")
    with Session().impersonate("alice"):
        older = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 10}, older["id"])
        newer = client.post("/api/chat/sessions").json()
        app_db.save_signal_snapshot({"foo": 20}, newer["id"])

    response = client.get(f"/api/projects/{hello_project}/users/alice/signal-history")

    assert response.status_code == 200
    body = response.json()
    assert [entry["values"] for entry in body] == [{"foo": 10}, {"foo": 20}]
    assert body[0]["timestamp"] <= body[1]["timestamp"]


def test_signal_history_excludes_rows_with_no_values(client, app_db, hello_project):
    app_db.set_active_project_name(hello_project, "bob")
    with Session().impersonate("bob"):
        session = client.get("/api/chat/session").json()
        turn = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}).json()
        client.put(
            f"/api/chat/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
        )

    response = client.get(f"/api/projects/{hello_project}/users/bob/signal-history")

    assert response.status_code == 200
    assert response.json() == []


def test_signal_history_is_scoped_to_the_given_user_and_project(client, app_db, hello_project):
    app_db.set_active_project_name(hello_project, "alice")
    with Session().impersonate("alice"):
        alice_session = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 1}, alice_session["id"])

    app_db.set_active_project_name(hello_project, "carol")
    with Session().impersonate("carol"):
        carol_session = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 2}, carol_session["id"])

    response = client.get(f"/api/projects/{hello_project}/users/alice/signal-history")

    assert response.status_code == 200
    body = response.json()
    assert body == [{"timestamp": body[0]["timestamp"], "values": {"foo": 1}}]
