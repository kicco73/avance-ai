from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from db import Db


@pytest.mark.contract
def test_bootstrap_creates_a_session(client, hello_project):
    response = client.get("/api/chat/session")

    assert response.status_code == 200
    body = response.json()
    assert body["project_name"] == hello_project
    assert body["open"] is True
    assert body["active"] is True
    assert body["has_annotations"] is False


@pytest.mark.regression
def test_sessions_list_reflects_has_annotations_per_session(client, hello_project):
    """has_annotations now reflects ChatSession.labeled directly (see
    ChatService.mark_session_labeled/"Label sessions" view's own "Mark
    done" button) rather than the old any-Tracking-row-has-an-annotation
    heuristic."""
    session = client.get("/api/chat/session").json()

    before = {s["id"]: s for s in client.get("/api/chat/sessions").json()}
    assert before[session["id"]]["has_annotations"] is False

    response = client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True

    after = {s["id"]: s for s in client.get("/api/chat/sessions").json()}
    assert after[session["id"]]["has_annotations"] is True
    # And the same single-session bootstrap endpoint reflects it too.
    assert client.get(f"/api/chat/session?session_id={session['id']}").json()["has_annotations"] is True


@pytest.mark.regression
def test_mark_session_labeled_works_for_an_imported_session(client, hello_project):
    """Regression for a real crash: an imported session (see ChatSession.
    source) always has datetime_end=None (see tracking.session_import's
    own create_chat_session call) — marking it done used to call
    ChatSessionManager.get_active_session against the 'imported' pool,
    which reached is_open's own `now - session["datetime_end"]` and threw
    TypeError: unsupported operand type(s) for -: 'datetime' and
    'NoneType'. An imported session is also never "active" at all (see
    ChatService.list_sessions' own docstring) — this locks that down too."""
    imported = client.post(
        "/api/chat/sessions/import", files={"file": ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain")}
    ).json()
    session_id = imported["session_id"]

    response = client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    assert response.status_code == 200
    body = response.json()
    assert body["has_annotations"] is True
    assert body["active"] is False

    # Toggles back off, same endpoint.
    response = client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": False})
    assert response.json()["has_annotations"] is False


@pytest.mark.regression
def test_manual_new_session_supersedes_the_bootstrap_one(client, hello_project):
    older = client.get("/api/chat/session").json()

    newer = client.post("/api/chat/sessions").json()

    assert newer["id"] != older["id"]
    assert newer["active"] is True

    sessions = {s["id"]: s for s in client.get("/api/chat/sessions").json()}
    # The core bug this guards against: both sessions are still "open"
    # (neither has expired), but only the most recently started one for
    # this project may ever be "active".
    assert sessions[older["id"]]["open"] is True
    assert sessions[older["id"]]["active"] is False
    assert sessions[newer["id"]]["open"] is True
    assert sessions[newer["id"]]["active"] is True


@pytest.mark.regression
# Currently failing against a real app bug: ChatService.process_turn() (see
# chat/chat_service.py:470-478, called directly by controller.py's
# post_message) never calls _require_active_session — unlike
# apply_manual_action (chat_service.py:452) — so a superseded-but-open
# session's chat turns are not rejected. Left failing per the task's own
# "leave a genuine app bug failing" rule.
def test_chat_turn_rejects_an_open_but_inactive_session(client, hello_project):
    older = client.get("/api/chat/session").json()
    client.post("/api/chat/sessions")  # supersedes `older`

    response = client.post("/api/chat/messages", json={"message": "hi", "session_id": older["id"]})

    assert response.status_code == 409
    assert "not active" in response.json()["error"]["message"].lower()


@pytest.mark.regression
def test_chat_turn_succeeds_against_the_active_session(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]})

    assert response.status_code == 200


@pytest.mark.regression
def test_manual_action_rejects_an_open_but_inactive_session(client, hello_project):
    older = client.get("/api/chat/session").json()
    client.post("/api/chat/sessions")  # supersedes `older`

    response = client.post("/api/action", json={"action_name": "chat", "session_id": older["id"]})

    assert response.status_code == 409
    assert "not active" in response.json()["error"]["message"].lower()


@pytest.mark.regression
# Currently failing against the same real app bug as
# test_chat_turn_rejects_an_open_but_inactive_session above: process_turn
# never validates session_id is still the active/open one for text chat
# turns (only apply_manual_action does, via _require_active_session).
def test_chat_turn_rejects_a_closed_session_without_auto_rotating(client, hello_project, app_db: Db):
    session = client.get("/api/chat/session").json()
    stale_end = datetime.utcnow() - timedelta(hours=2)
    app_db.touch_chat_session(session["id"], stale_end, session["end_state"])

    response = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]})

    assert response.status_code == 409
    # No silent rotation: the sessions list must still show only the one
    # (now closed) session — nothing new was created on its behalf.
    sessions = client.get("/api/chat/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]


@pytest.mark.regression
def test_delete_session_removes_it(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.delete(f"/api/chat/sessions/{session['id']}")
    assert response.status_code == 200

    assert client.get("/api/chat/sessions").json() == []


@pytest.mark.contract
def test_delete_session_rejects_unknown_id(client, hello_project):
    response = client.delete("/api/chat/sessions/999999")
    assert response.status_code == 404


@pytest.mark.regression
def test_manual_new_session_starts_at_the_automatons_initial_state_not_the_current_one(client):
    """Regression test: a brand new session represents starting the
    conversation over, so it must be recorded as starting at the
    automaton's own init_action.target — not wherever the project's
    shared, project-wide automaton position has since moved to (that's a
    separate fact, untouched by creating a session; see
    ChatSession.start_state's own docs)."""
    samples_dir = Path(__file__).resolve().parent.parent / "samples" / "projects"
    content = (samples_dir / "Aprendr català.zip").read_bytes()
    resp = client.put("/api/projects/cat", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    client.put("/api/projects/cat/activate")
    client.post("/api/projects/cat/publish", json={})

    bootstrap = client.get("/api/chat/session").json()
    assert bootstrap["start_state"] == "welcome"  # this project's init_action.target

    # Move the project's automaton away from its initial state.
    action_response = client.post(
        "/api/action", json={"action_name": "unit-subjuntive", "session_id": bootstrap["id"]}
    )
    assert action_response.status_code == 200
    assert action_response.json()["state"]["key"] != "welcome"

    new_session = client.post("/api/chat/sessions").json()

    assert new_session["start_state"] == "welcome"


@pytest.mark.regression
def test_switching_projects_does_not_delete_the_previous_projects_sessions(client, app_db: Db):
    # Uploads both sample projects directly (avoids depending on a
    # single-project fixture, since this test needs two).
    samples_dir = Path(__file__).resolve().parent.parent / "samples" / "projects"
    for name, sample in (("hello", "Hello world.zip"), ("cat", "Aprendr català.zip")):
        content = (samples_dir / sample).read_bytes()
        resp = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 200, resp.text
        resp = client.post(f"/api/projects/{name}/publish", json={})
        assert resp.status_code == 200, resp.text

    client.put("/api/projects/hello/activate")
    session = client.get("/api/chat/session").json()

    client.put("/api/projects/cat/activate")

    # Regression: switching the active project must never touch another
    # project's sessions — verified straight against the persistence
    # layer, independent of whatever the (now inactive) project's own
    # /api/chat/sessions listing shows.
    assert app_db.get_chat_session(session["id"]) is not None
