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
    """has_annotations reflects ChatSession.labeled directly, per session."""
    session = client.get("/api/chat/session").json()

    before = {s["id"]: s for s in client.get("/api/projects/hello/sessions").json()}
    assert before[session["id"]]["has_annotations"] is False

    response = client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True

    after = {s["id"]: s for s in client.get("/api/projects/hello/sessions").json()}
    assert after[session["id"]]["has_annotations"] is True
    # The single-session bootstrap endpoint reflects it too.
    assert client.get(f"/api/chat/session?session_id={session['id']}").json()["has_annotations"] is True


@pytest.mark.regression
def test_mark_session_labeled_works_for_an_imported_session(client, hello_project):
    """Regression: an imported session always has datetime_end=None, which
    must not crash is_open's active-session check. An imported session is
    also never "active"."""
    imported = client.post(
        "/api/projects/hello/sessions/import", files={"file": ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain")}
    ).json()
    session_id = imported["session_id"]

    response = client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    assert response.status_code == 200
    body = response.json()
    assert body["has_annotations"] is True
    assert body["active"] is False

    response = client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": False})
    assert response.json()["has_annotations"] is False


@pytest.mark.regression
def test_put_session_title_renames_and_reflects_in_the_list(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.put(f"/api/chat/sessions/{session['id']}/title", json={"title": "My session"})
    assert response.status_code == 200
    assert response.json()["title"] == "My session"

    listed = {s["id"]: s for s in client.get("/api/projects/hello/sessions").json()}
    assert listed[session["id"]]["title"] == "My session"


@pytest.mark.regression
def test_put_session_title_blank_clears_it_back_to_none(client, hello_project):
    session = client.get("/api/chat/session").json()
    client.put(f"/api/chat/sessions/{session['id']}/title", json={"title": "Named"})

    response = client.put(f"/api/chat/sessions/{session['id']}/title", json={"title": "   "})

    assert response.json()["title"] is None


@pytest.mark.regression
def test_put_session_comment_round_trips_and_clears(client, hello_project):
    session = client.get("/api/chat/session").json()
    assert client.get("/api/chat/session").json()["comment"] is None

    response = client.put(f"/api/chat/sessions/{session['id']}/comment", json={"comment": "Worth a second look."})
    assert response.status_code == 200
    assert response.json()["comment"] == "Worth a second look."

    response = client.put(f"/api/chat/sessions/{session['id']}/comment", json={"comment": None})
    assert response.json()["comment"] is None


@pytest.mark.regression
def test_put_session_title_and_comment_work_for_an_imported_session(client, hello_project):
    """An imported session's datetime_end=None must not break the shared
    active-resolution path used by title/comment updates."""
    imported = client.post(
        "/api/projects/hello/sessions/import", files={"file": ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain")}
    ).json()
    session_id = imported["session_id"]

    title_resp = client.put(f"/api/chat/sessions/{session_id}/title", json={"title": "Renamed import"})
    assert title_resp.status_code == 200
    assert title_resp.json()["active"] is False

    comment_resp = client.put(f"/api/chat/sessions/{session_id}/comment", json={"comment": "note"})
    assert comment_resp.status_code == 200
    assert comment_resp.json()["comment"] == "note"


@pytest.mark.contract
def test_put_session_title_rejects_an_unknown_session(client, hello_project):
    response = client.put("/api/chat/sessions/999999/title", json={"title": "x"})
    assert response.status_code == 404


@pytest.mark.regression
def test_manual_new_session_supersedes_the_bootstrap_one(client, hello_project):
    older = client.get("/api/chat/session").json()

    newer = client.post("/api/chat/sessions").json()

    assert newer["id"] != older["id"]
    assert newer["active"] is True

    sessions = {s["id"]: s for s in client.get("/api/projects/hello/sessions").json()}
    # Both sessions are still "open" (neither has expired), but only the
    # most recently started one may ever be "active".
    assert sessions[older["id"]]["open"] is True
    assert sessions[older["id"]]["active"] is False
    assert sessions[newer["id"]]["open"] is True
    assert sessions[newer["id"]]["active"] is True


@pytest.mark.regression
# Currently failing: ChatService.process_turn() never calls
# _require_active_session, so a superseded-but-open session's chat turns
# are not rejected, unlike apply_manual_action.
def test_chat_turn_rejects_an_open_but_inactive_session(client, hello_project):
    older = client.get("/api/chat/session").json()
    client.post("/api/chat/sessions")  # supersedes `older`

    response = client.post(f"/api/chat/sessions/{older['id']}/messages", json={"message": "hi"})

    assert response.status_code == 409
    assert "not active" in response.json()["error"]["message"].lower()


@pytest.mark.regression
def test_chat_turn_succeeds_against_the_active_session(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200


@pytest.mark.regression
def test_manual_action_rejects_an_open_but_inactive_session(client, hello_project):
    older = client.get("/api/chat/session").json()
    client.post("/api/chat/sessions")  # supersedes `older`

    response = client.post(f"/api/chat/sessions/{older['id']}/action", json={"action_name": "chat"})

    assert response.status_code == 409
    assert "not active" in response.json()["error"]["message"].lower()


@pytest.mark.regression
# Currently failing against the same bug as
# test_chat_turn_rejects_an_open_but_inactive_session above.
def test_chat_turn_rejects_a_closed_session_without_auto_rotating(client, hello_project, app_db: Db):
    session = client.get("/api/chat/session").json()
    stale_end = datetime.utcnow() - timedelta(hours=2)
    app_db.touch_chat_session(session["id"], stale_end, session["end_state"])

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    assert response.status_code == 409
    # No silent rotation: nothing new was created on the closed session's behalf.
    sessions = client.get("/api/projects/hello/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]


@pytest.mark.regression
def test_delete_session_removes_it(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.delete(f"/api/chat/sessions/{session['id']}")
    assert response.status_code == 200

    assert client.get("/api/projects/hello/sessions").json() == []


@pytest.mark.contract
def test_delete_session_rejects_unknown_id(client, hello_project):
    response = client.delete("/api/chat/sessions/999999")
    assert response.status_code == 404


@pytest.mark.regression
def test_manual_new_session_starts_at_the_automatons_current_state_not_the_initial_one(client):
    """A brand new session must start wherever the project's shared
    automaton position currently sits, never silently rewound to
    init_action.target."""
    samples_dir = Path(__file__).resolve().parent.parent / "samples" / "projects"
    content = (samples_dir / "Aprendr català.zip").read_bytes()
    resp = client.put("/api/projects/cat", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    client.put("/api/projects/cat/activate")
    client.post("/api/projects/cat/publish", json={})

    bootstrap = client.get("/api/chat/session").json()
    assert bootstrap["start_state"] == "welcome"  # this project's init_action.target

    # Move the automaton away from its initial state.
    action_response = client.post(f"/api/chat/sessions/{bootstrap['id']}/action", json={"action_name": "unit-subjuntive"})
    assert action_response.status_code == 200
    current_state = action_response.json()["state"]["key"]
    assert current_state != "welcome"

    new_session = client.post("/api/chat/sessions").json()

    assert new_session["start_state"] == current_state


@pytest.mark.regression
def test_switching_projects_does_not_delete_the_previous_projects_sessions(client, app_db: Db):
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

    # Switching the active project must never touch another project's sessions.
    assert app_db.get_chat_session(session["id"]) is not None


@pytest.mark.regression
def test_sessions_list_is_scoped_by_the_url_never_the_active_project(client):
    """GET /api/projects/{project_name}/sessions must return that exact
    project's own sessions regardless of which project is currently
    active."""
    samples_dir = Path(__file__).resolve().parent.parent / "samples" / "projects"
    for name, sample in (("hello", "Hello world.zip"), ("cat", "Aprendr català.zip")):
        content = (samples_dir / sample).read_bytes()
        resp = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 200, resp.text
        resp = client.post(f"/api/projects/{name}/publish", json={})
        assert resp.status_code == 200, resp.text

    client.put("/api/projects/hello/activate")
    hello_session = client.get("/api/chat/session").json()

    client.put("/api/projects/cat/activate")

    # "cat" is now active, but the URL decides, not the active project.
    explicit_hello = client.get("/api/projects/hello/sessions").json()
    assert [s["id"] for s in explicit_hello] == [hello_session["id"]]
    assert all(s["project_name"] == "hello" for s in explicit_hello)

    explicit_cat = client.get("/api/projects/cat/sessions").json()
    assert all(s["project_name"] == "cat" for s in explicit_cat)
