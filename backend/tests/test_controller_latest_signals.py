"""Integration tests for GET /api/projects/{project_name}/users/{username}/latest-signals,
exercising ChatService.get_latest_signal_values end to end: the user's own
last live session (for the current-state card), and the last valid,
non-null signal snapshot found anywhere across their session history —
searching further back when the most recent session never captured one.
"""
from __future__ import annotations

import json

import pytest

from session import Session

from conftest import parse_chat_turn_sse

pytestmark = pytest.mark.contract


def test_latest_signals_returns_the_most_recent_sessions_latest_snapshot(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with Session().impersonate("alice"):
        session = client.get("/api/chat/session").json()
        turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
        app_db.save_signal_snapshot({"foo": 1}, session["id"])
        app_db.save_signal_snapshot({"foo": 42}, session["id"], message_id=turn["assistant_message_id"])

    response = client.get(f"/api/projects/{hello_project}/users/alice/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session["id"]
    assert body["last_session"]["id"] == session["id"]
    assert json.loads(body["values"]) == {"foo": 42}


def test_latest_signals_falls_back_to_an_earlier_session_when_the_latest_has_none(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "carol")
    with Session().impersonate("carol"):
        older = client.get("/api/chat/session").json()
        app_db.save_signal_snapshot({"foo": 7}, older["id"])
        newer = client.post("/api/chat/sessions").json()

    response = client.get(f"/api/projects/{hello_project}/users/carol/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["last_session"]["id"] == newer["id"]
    assert body["session_id"] == older["id"]
    assert json.loads(body["values"]) == {"foo": 7}


def test_latest_signals_has_no_values_for_a_session_with_no_signal_snapshot(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "bob")
    with Session().impersonate("bob"):
        session = client.get("/api/chat/session").json()

    response = client.get(f"/api/projects/{hello_project}/users/bob/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["last_session"]["id"] == session["id"]
    assert body["session_id"] == session["id"]
    assert body["values"] is None


def test_latest_signals_is_none_for_a_user_with_no_sessions(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/users/nobody/latest-signals")

    assert response.status_code == 200
    assert response.json() == {"last_session": None, "session_id": None, "values": None}
