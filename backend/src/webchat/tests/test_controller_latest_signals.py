from __future__ import annotations

import json

import pytest

from system.web_session import WebSession

from conftest import chat_turn, enter_chat, session_of
from webchat.tests.webchat_helpers import new_chat

pytestmark = pytest.mark.contract


def test_latest_signals_returns_the_most_recent_sessions_latest_snapshot(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "alice")
    with WebSession().impersonate("alice"):
        session_id = session_of(enter_chat(client, hello_project))
        turn = chat_turn(client, session_id, "hi")
        app_db.save_signal_snapshot({"foo": 1}, session_id)
        app_db.save_signal_snapshot({"foo": 42}, session_id, message_id=turn["assistant_message_id"])

    response = client.get(f"/api/core/projects/{hello_project}/users/alice/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["last_session"]["id"] == session_id
    assert json.loads(body["values"]) == {"foo": 42}


def test_latest_signals_falls_back_to_an_earlier_session_when_the_latest_has_none(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "carol")
    with WebSession().impersonate("carol"):
        older = session_of(enter_chat(client, hello_project))
        app_db.save_signal_snapshot({"foo": 7}, older)
        newer = new_chat(client, hello_project)

    response = client.get(f"/api/core/projects/{hello_project}/users/carol/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["last_session"]["id"] == newer
    assert body["session_id"] == older
    assert json.loads(body["values"]) == {"foo": 7}


def test_latest_signals_carries_each_signals_last_value_past_empty_and_partial_snapshots(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "dave")
    with WebSession().impersonate("dave"):
        older = session_of(enter_chat(client, hello_project))
        app_db.save_signal_snapshot({"foo": 7, "bar": 3}, older)
        newer = new_chat(client, hello_project)
        app_db.save_signal_snapshot({"foo": 9}, newer)
        app_db.save_signal_snapshot({}, newer)

    response = client.get(f"/api/core/projects/{hello_project}/users/dave/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == newer
    assert json.loads(body["values"]) == {"foo": 9, "bar": 3}


def test_latest_signals_reads_values_from_test_sessions_like_the_timeline(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "erin")
    with WebSession().impersonate("erin"):
        live = session_of(enter_chat(client, hello_project))
        app_db.save_signal_snapshot({}, live)
    test_session = app_db.create_chat_session(
        "erin", hello_project, app_db.get_project_revision(hello_project), type="test",
    )
    app_db.save_signal_snapshot({"foo": 55}, test_session)

    response = client.get(f"/api/core/projects/{hello_project}/users/erin/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["last_session"]["id"] == live
    assert json.loads(body["values"]) == {"foo": 55}


def test_latest_signals_has_no_values_for_a_session_with_no_signal_snapshot(client, app_db, hello_project):
    app_db.set_active_project_id(hello_project, "bob")
    with WebSession().impersonate("bob"):
        session_id = session_of(enter_chat(client, hello_project))

    response = client.get(f"/api/core/projects/{hello_project}/users/bob/latest-signals")

    assert response.status_code == 200
    body = response.json()
    assert body["last_session"]["id"] == session_id
    assert body["session_id"] == session_id
    assert body["values"] is None


def test_latest_signals_is_none_for_a_user_with_no_sessions(client, hello_project):
    response = client.get(f"/api/core/projects/{hello_project}/users/nobody/latest-signals")

    assert response.status_code == 200
    assert response.json() == {"last_session": None, "session_id": None, "values": None}
