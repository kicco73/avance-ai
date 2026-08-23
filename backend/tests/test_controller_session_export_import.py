"""GET /api/projects/{project_name}/sessions/export and POST .../import
— the "Label sessions" view's own "Download all"/upload pair (see
tracking/session_export.py's own module docstring for the exact shape).
"""
from __future__ import annotations

import json

import pytest

from session import Session

pytestmark = pytest.mark.contract


def _import_transcript(client, text="user: hi there\nassistant: hello, world!\n", title="transcript.txt"):
    response = client.post(
        "/api/projects/hello/sessions/import", files=[("files", (title, text, "text/plain"))]
    )
    assert response.status_code == 200, response.text
    return response.json()["last_session_id"]


def _import_json(client, sessions: list[dict], filename="sessions.json"):
    response = client.post(
        "/api/projects/hello/sessions/import", files=[("files", (filename, json.dumps(sessions), "application/json"))]
    )
    assert response.status_code == 200, response.text
    return response.json()


def _messages(client, session_id):
    return client.get(f"/api/chat/sessions/{session_id}/messages").json()


@pytest.mark.regression
def test_export_sessions_is_empty_for_a_project_with_no_sessions(client, hello_project):
    response = client.get("/api/projects/hello/sessions/export")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.regression
def test_export_sessions_includes_native_sessions_alongside_imported_ones(client, hello_project):
    """Export must include every session of the project — native (live
    chat) and imported alike, not just the imported ones."""
    native_session = client.get("/api/chat/session").json()
    _import_transcript(client)

    response = client.get("/api/projects/hello/sessions/export")
    assert response.status_code == 200
    exported = response.json()

    assert len(exported) == 2
    exported_starts = {e["start_state"] for e in exported}
    assert native_session["start_state"] in exported_starts


@pytest.mark.regression
def test_export_sessions_reflects_an_imported_session_and_its_annotations(client, hello_project):
    session_id = _import_transcript(client)
    user_message_id = _messages(client, session_id)[0]["id"]
    client.put(f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "Hello"})
    client.put(f"/api/chat/messages/{user_message_id}/comment", json={"comment": "worth reviewing"})
    client.put(f"/api/chat/sessions/{session_id}/title", json={"title": "My export"})
    client.put(f"/api/chat/sessions/{session_id}/comment", json={"comment": "session-wide note"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    response = client.get("/api/projects/hello/sessions/export")
    assert response.status_code == 200
    [exported] = response.json()

    assert exported["name"] == "My export"
    assert exported["comment"] == "session-wide note"
    assert exported["labeled"] is True
    assert exported["timestamp"] is None  # an imported session never has one
    assert len(exported["messages"]) == 2
    first = exported["messages"][0]
    assert first["role"] == "user"
    assert first["text"] == "hi there"
    assert first["expected_state"] == "Hello"
    assert first["comment"] == "worth reviewing"


@pytest.mark.regression
def test_import_json_round_trips_an_exported_session_exactly(client, hello_project):
    session_id = _import_transcript(client)
    user_message_id = _messages(client, session_id)[0]["id"]
    client.put(f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "Hello"})
    client.put(f"/api/chat/sessions/{session_id}/title", json={"title": "Original"})
    [exported] = client.get("/api/projects/hello/sessions/export").json()

    result = _import_json(client, [exported])
    assert result["results"] == [{"file": "Original", "ok": True, "session_id": result["last_session_id"]}]
    assert result["last_session_id"] != session_id

    [_, reimported] = client.get("/api/projects/hello/sessions/export").json()
    assert reimported["name"] == "Original"
    assert reimported["messages"] == exported["messages"]


@pytest.mark.regression
def test_reimporting_the_same_export_twice_for_the_same_user_is_a_no_op_the_second_time(client, hello_project):
    payload = {
        "name": "Real session",
        "username": "User 1",
        "timestamp": "2026-01-01T10:00:00+00:00",
        "datetime_end": "2026-01-01T10:05:00+00:00",
        "start_state": "Hello",
        "end_state": "Hello",
        "labeled": False,
        "comment": None,
        "messages": [{"role": "user", "text": "hi", "timestamp": "2026-01-01T10:00:00+00:00"}],
    }
    first = _import_json(client, [payload])
    assert first["results"] == [{"file": "Real session", "ok": True, "session_id": first["last_session_id"]}]

    second = _import_json(client, [payload])
    assert second["results"][0]["ok"] is False
    assert second["last_session_id"] is None

    Session().user = "User 1"
    sessions = client.get("/api/projects/hello/sessions?include_imported=true").json()
    assert len(sessions) == 1


@pytest.mark.regression
def test_import_json_restores_a_native_looking_session_with_real_timestamps(client, hello_project):
    payload = {
        "name": "Real session",
        "username": "User 1",
        "timestamp": "2026-01-01T10:00:00+00:00",
        "datetime_end": "2026-01-01T10:05:00+00:00",
        "start_state": "Hello",
        "end_state": "Hello",
        "labeled": False,
        "comment": None,
        "messages": [
            {"role": "user", "text": "hi", "timestamp": "2026-01-01T10:00:00+00:00"},
            {
                "role": "assistant", "text": "hello", "timestamp": "2026-01-01T10:00:05+00:00",
                "old_state": "Hello", "action": "reply", "new_state": "Hello",
                "values": {"mood": 0.5},
            },
        ],
    }

    result = _import_json(client, [payload])
    session_id = result["last_session_id"]

    Session().user = "User 1"
    [exported] = client.get("/api/projects/hello/sessions/export").json()
    assert exported["timestamp"] == "2026-01-01T10:00:00+00:00"
    assert exported["start_state"] == "Hello"
    assert exported["end_state"] == "Hello"
    assert exported["messages"][1]["values"] == {"mood": 0.5}
    assert exported["messages"][1]["new_state"] == "Hello"

    sessions = {s["id"]: s for s in client.get("/api/projects/hello/sessions?include_imported=true").json()}
    assert sessions[session_id]["type"] == "imported"


@pytest.mark.regression
def test_import_json_handles_a_mixed_batch_of_files(client, hello_project):
    """One .txt transcript and one .json export (with a good and a bad
    session) uploaded together in a single request — the bad session is
    skipped without aborting either the rest of the array or the batch."""
    response = client.post(
        "/api/projects/hello/sessions/import",
        files=[
            ("files", ("t.txt", "user: hi\nassistant: yo\n", "text/plain")),
            ("files", ("more.json", json.dumps([
                {"messages": [{"role": "user"}]},  # missing required 'text' — malformed
                {"name": "Good one", "username": "User 1", "messages": [{"role": "user", "text": "hi"}]},
            ]), "application/json")),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    by_ok = {r["ok"] for r in body["results"]}
    assert by_ok == {True, False}
    assert len(body["results"]) == 3

    Session().user = "User 1"
    sessions = client.get("/api/projects/hello/sessions?include_imported=true").json()
    titles = {s["title"] for s in sessions}
    assert "t.txt" in titles
    assert "Good one" in titles


@pytest.mark.contract
def test_import_json_rejects_a_malformed_message(client, hello_project):
    result = _import_json(client, [{"name": "bad", "messages": [{"role": "user"}]}])  # missing required 'text'
    assert result["results"] == [{"file": "bad", "ok": False, "error": result["results"][0]["error"]}]
    assert "text" in result["results"][0]["error"]
    assert result["last_session_id"] is None
