"""Integration tests for GET /api/core/projects/{project_name}/users/{username}/timeline,
exercising TurnService.get_timeline end to end: every real signal
snapshot and state transition for a user across their whole session
history, chronological — Manage Users' Timeline tab.
"""
from __future__ import annotations

import pytest

from system.web_session import WebSession

from conftest import chat_turn, enter_chat, session_of
from webchat.tests.webchat_helpers import new_chat

pytestmark = pytest.mark.contract


def test_timeline_signals_span_every_session_chronologically(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with WebSession().impersonate("alice"):
        older = session_of(enter_chat(client, hello_project))
        app_db.save_signal_snapshot({"foo": 10}, older)
        newer = new_chat(client, hello_project)
        app_db.save_signal_snapshot({"foo": 20}, newer)

    response = client.get(f"/api/core/projects/{hello_project}/users/alice/timeline")

    assert response.status_code == 200
    body = response.json()
    assert [entry["values"] for entry in body["signals"]] == [{"foo": 10}, {"foo": 20}]
    assert body["signals"][0]["timestamp"] <= body["signals"][1]["timestamp"]


def test_timeline_excludes_signal_rows_but_still_includes_the_initial_state(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "bob")
    with WebSession().impersonate("bob"):
        session_id = session_of(enter_chat(client, hello_project))
        turn = chat_turn(client, session_id, "hi")
        client.put(
            f"/api/skills/platform/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
        )

    response = client.get(f"/api/core/projects/{hello_project}/users/bob/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == []
    assert [t["new_state"] for t in body["transitions"]] == ["Hello"]


def test_timeline_includes_state_transitions(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with WebSession().impersonate("alice"):
        session_id = session_of(enter_chat(client, hello_project))
        app_db.save_transition(None, "leave", "Goodbye", session_id, "INFO")

    response = client.get(f"/api/core/projects/{hello_project}/users/alice/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == []
    assert [t["new_state"] for t in body["transitions"]] == ["Hello", "Goodbye"]


def test_timeline_is_scoped_to_the_given_user_and_project(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with WebSession().impersonate("alice"):
        alice_session = session_of(enter_chat(client, hello_project))
        app_db.save_signal_snapshot({"foo": 1}, alice_session)

    app_db.set_active_project_id(hello_project, "carol")
    with WebSession().impersonate("carol"):
        carol_session = session_of(enter_chat(client, hello_project))
        app_db.save_signal_snapshot({"foo": 2}, carol_session)

    response = client.get(f"/api/core/projects/{hello_project}/users/alice/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == [{"timestamp": body["signals"][0]["timestamp"], "values": {"foo": 1}}]
