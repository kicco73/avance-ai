"""GET /api/projects/{project_name}/sessions/export and POST .../import
— the "Label sessions" view's own "Download all"/upload pair (see
tracking/session_export.py's own module docstring for the exact shape).
"""
from __future__ import annotations

import json

import pytest

from conftest import parse_sse_result
from session import Session

pytestmark = pytest.mark.contract


def _import_transcript(client, project, text="user: hi there\nassistant: hello, world!\n", title="transcript.txt"):
    response = client.post(
        f"/api/projects/{project}/sessions/import", files=[("files", (title, text, "text/plain"))]
    )
    assert response.status_code == 200, response.text
    return parse_sse_result(response)["last_session_id"]


def _import_json(client, project, sessions: list[dict], filename="sessions.json"):
    response = client.post(
        f"/api/projects/{project}/sessions/import", files=[("files", (filename, json.dumps(sessions), "application/json"))]
    )
    assert response.status_code == 200, response.text
    return parse_sse_result(response)


def _messages(client, session_id):
    return client.get(f"/api/chat/sessions/{session_id}/messages").json()


def _export(client, project) -> list[dict]:
    response = client.get(f"/api/projects/{project}/sessions/export")
    assert response.status_code == 200
    return response.json()


@pytest.mark.regression
def test_export_covers_every_session_of_the_project_native_and_imported_alike_with_its_own_annotations(client, hello_project):
    """Export must include every session of the project — native (live
    chat) and imported alike, not just the imported ones."""
    assert _export(client, hello_project) == []

    native_session = client.get("/api/chat/session").json()
    session_id = _import_transcript(client, hello_project)
    user_message_id = _messages(client, session_id)[0]["id"]
    client.put(f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "Hello"})
    client.put(f"/api/chat/messages/{user_message_id}/comment", json={"comment": "worth reviewing"})
    client.put(f"/api/chat/sessions/{session_id}/title", json={"title": "My export"})
    client.put(f"/api/chat/sessions/{session_id}/comment", json={"comment": "session-wide note"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    exported = _export(client, hello_project)
    assert len(exported) == 2
    assert native_session["start_state"] in {e["start_state"] for e in exported}

    [imported] = [e for e in exported if e["name"] == "My export"]
    assert imported["comment"] == "session-wide note"
    assert imported["labeled"] is True
    assert imported["timestamp"] is None  # an imported session never has one
    assert len(imported["messages"]) == 2
    first = imported["messages"][0]
    assert first["role"] == "user"
    assert first["text"] == "hi there"
    assert first["expected_state"] == "Hello"
    assert first["comment"] == "worth reviewing"


@pytest.mark.regression
def test_an_export_round_trips_back_through_import_with_its_messages_and_their_own_tool_calls_intact(client, app_db, hello_project):
    session_id = _import_transcript(client, hello_project)
    user_message_id = _messages(client, session_id)[0]["id"]
    client.put(f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "Hello"})
    client.put(f"/api/chat/sessions/{session_id}/title", json={"title": "Original"})
    tool_calls = [{"name": "source_flights_select", "arguments": {"value": "paris"}, "result": "row"}]
    app_db.record_tool_calls(session_id, tool_calls, message_id=user_message_id)

    [exported] = _export(client, hello_project)
    assert exported["messages"][0]["tool_calls"] == tool_calls

    result = _import_json(client, hello_project, [exported])
    assert result["results"] == [{"file": "Original", "ok": True, "session_id": result["last_session_id"]}]
    assert result["last_session_id"] != session_id

    [_, reimported] = _export(client, hello_project)
    assert reimported["name"] == "Original"
    assert reimported["messages"] == exported["messages"]


@pytest.mark.regression
def test_a_native_looking_json_session_restores_its_timestamps_states_values_and_tokens_and_reimporting_it_is_a_no_op(client, hello_project):
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
            {"role": "user", "text": "hi", "timestamp": "2026-01-01T10:00:00+00:00", "tokens": 42},
            {
                "role": "assistant", "text": "hello", "timestamp": "2026-01-01T10:00:05+00:00",
                "old_state": "Hello", "action": "reply", "new_state": "Hello",
                "values": {"mood": 0.5},
            },
        ],
    }

    first = _import_json(client, hello_project, [payload])
    session_id = first["last_session_id"]
    assert first["results"] == [{"file": "Real session", "ok": True, "session_id": session_id}]

    second = _import_json(client, hello_project, [payload])
    assert second["results"][0]["ok"] is False
    assert second["last_session_id"] is None

    Session().user = "User 1"
    [exported] = _export(client, hello_project)
    assert exported["timestamp"] == "2026-01-01T10:00:00+00:00"
    assert exported["start_state"] == "Hello"
    assert exported["end_state"] == "Hello"
    assert exported["messages"][0]["tokens"] == 42
    assert "tokens" not in exported["messages"][1]  # omitted when unknown
    assert exported["messages"][1]["values"] == {"mood": 0.5}
    assert exported["messages"][1]["new_state"] == "Hello"

    sessions = client.get(f"/api/projects/{hello_project}/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert {s["id"]: s for s in sessions}[session_id]["type"] == "imported"


@pytest.mark.contract
def test_closed_at_close_reason_and_origin_round_trip_and_default_to_none_when_absent(client, hello_project):
    closed = {
        "name": "Closed session",
        "username": "User 1",
        "timestamp": "2026-01-01T10:00:00+00:00",
        "datetime_end": "2026-01-01T10:05:00+00:00",
        "start_state": "Hello",
        "end_state": "Hello",
        "closed_at": "2026-01-01T10:05:00+00:00",
        "close_reason": "manual-user",
        "messages": [
            {"role": "user", "text": "hi", "timestamp": "2026-01-01T10:00:00+00:00"},
            {
                "role": "assistant", "text": "hello", "timestamp": "2026-01-01T10:00:05+00:00",
                "old_state": "Hello", "action": "reply", "new_state": "Hello", "origin": "trigger",
            },
        ],
    }
    never_closed = {
        "name": "Never closed",
        "username": "User 2",
        "messages": [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "hello", "old_state": "Hello", "action": "reply", "new_state": "Hello"},
        ],
    }

    assert _import_json(client, hello_project, [closed])["results"][0]["ok"] is True
    Session().user = "User 1"
    [exported] = _export(client, hello_project)
    assert exported["closed_at"] == "2026-01-01T10:05:00+00:00"
    assert exported["close_reason"] == "manual-user"
    assert exported["messages"][1]["origin"] == "trigger"

    Session().user = "user"
    _import_json(client, hello_project, [never_closed])
    Session().user = "User 2"
    [bare] = _export(client, hello_project)
    assert bare["closed_at"] is None
    assert bare["close_reason"] is None
    assert bare["messages"][1]["origin"] is None


@pytest.mark.regression
def test_a_mixed_batch_skips_only_the_malformed_sessions_without_aborting_the_rest(client, hello_project):
    """One .txt transcript and one .json export (with a good and a bad
    session) uploaded together in a single request — the bad session is
    skipped without aborting either the rest of the array or the batch."""
    response = client.post(
        f"/api/projects/{hello_project}/sessions/import",
        files=[
            ("files", ("t.txt", "user: hi\nassistant: yo\n", "text/plain")),
            ("files", ("more.json", json.dumps([
                {"messages": [{"role": "user"}]},  # missing required 'text' — malformed
                {"name": "Good one", "username": "User 1", "messages": [{"role": "user", "text": "hi"}]},
            ]), "application/json")),
        ],
    )
    assert response.status_code == 200, response.text
    body = parse_sse_result(response)

    assert len(body["results"]) == 3
    assert {r["ok"] for r in body["results"]} == {True, False}

    Session().user = "User 1"
    titles = {s["title"] for s in client.get(f"/api/projects/{hello_project}/sessions?include_imported=true").json()}
    assert "t.txt" in titles
    assert "Good one" in titles


@pytest.mark.contract
def test_a_malformed_message_is_rejected_naming_the_missing_field(client, hello_project):
    result = _import_json(client, hello_project, [{"name": "bad", "messages": [{"role": "user"}]}])

    assert result["results"] == [{"file": "bad", "ok": False, "error": result["results"][0]["error"]}]
    assert "text" in result["results"][0]["error"]
    assert result["last_session_id"] is None
