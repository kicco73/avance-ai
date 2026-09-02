from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from chat.channels import NATIVE_CHAT, WHATSAPP_CHAT
from conftest import parse_chat_turn_sse_error
from conftest import parse_sse_result
from db import Db
from session import Session

CHANNEL_CODES_PROJECT_YAML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: advance
        ui-label: Advance
        ui-button: Advance
        target: b
  b:
    ui-label: B
    contextual-prompt: bye
    chat: false
"""


def _setup_channel_codes_project(app_db, project_name="channel-codes-proj"):
    app_db.ensure_project(project_name)
    app_db.save_project_files(
        project_name, {"index.yml": CHANNEL_CODES_PROJECT_YAML.encode("utf-8")}, {"index.yml": "text/yaml"},
    )
    app_db.publish_project(project_name)
    app_db.set_active_project_name(project_name, "user")


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

    before = {s["id"]: s for s in client.get(f"/api/projects/{hello_project}/sessions").json()}
    assert before[session["id"]]["has_annotations"] is False

    response = client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True

    after = {s["id"]: s for s in client.get(f"/api/projects/{hello_project}/sessions").json()}
    assert after[session["id"]]["has_annotations"] is True
    # The single-session bootstrap endpoint reflects it too.
    assert client.get(f"/api/chat/session?session_id={session['id']}").json()["has_annotations"] is True


@pytest.mark.regression
def test_mark_session_labeled_works_for_an_imported_session(client, hello_project):
    """Regression: an imported session always has datetime_end=None, which
    must not crash is_open's active-session check. An imported session is
    also never "active"."""
    imported = client.post(
        f"/api/projects/{hello_project}/sessions/import", files=[("files", ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    session_id = parse_sse_result(imported)["last_session_id"]

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

    listed = {s["id"]: s for s in client.get(f"/api/projects/{hello_project}/sessions").json()}
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
        f"/api/projects/{hello_project}/sessions/import", files=[("files", ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    session_id = parse_sse_result(imported)["last_session_id"]

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

    sessions = {s["id"]: s for s in client.get(f"/api/projects/{hello_project}/sessions").json()}
    # "New session" explicitly closes whatever was open before creating
    # the new one — the older session is closed, not just superseded.
    assert sessions[older["id"]]["open"] is False
    assert sessions[older["id"]]["active"] is False
    assert sessions[older["id"]]["close_reason"] == "force-new-session"
    assert sessions[newer["id"]]["open"] is True
    assert sessions[newer["id"]]["active"] is True


@pytest.mark.regression
def test_chat_turn_rejects_a_session_closed_by_a_manual_new_session(client, hello_project):
    older = client.get("/api/chat/session").json()
    client.post("/api/chat/sessions")  # closes `older` (force-new-session) and supersedes it

    response = client.post(f"/api/chat/sessions/{older['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200  # the turn endpoint always streams 200; failures arrive as an SSE `error` event
    error = parse_chat_turn_sse_error(response)
    assert "closed" in error["message"].lower()
    assert error["code"] == "session_closed"


def _someone_elses_session(app_db, project_name="channel-codes-proj"):
    """A real row, but not `user`'s own — require_active_session's
    session_not_found (409), distinct from _project_name_for_session's
    own earlier "Session not found." (404) for a session_id that isn't a
    real row at all."""
    return app_db.create_chat_session(
        "someone-else", project_name, app_db.get_project_published_revision(project_name),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a", type="live", channel=NATIVE_CHAT,
    )


@pytest.mark.contract
def test_chat_turn_exposes_session_not_found_code(client, app_db):
    _setup_channel_codes_project(app_db)
    session_id = _someone_elses_session(app_db)

    response = client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})

    assert response.status_code == 200
    assert parse_chat_turn_sse_error(response)["code"] == "session_not_found"


@pytest.mark.contract
def test_manual_action_exposes_session_not_found_code(client, app_db):
    _setup_channel_codes_project(app_db)
    session_id = _someone_elses_session(app_db)

    response = client.post(f"/api/chat/sessions/{session_id}/action", json={"action_name": "advance"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.contract
def test_manual_action_exposes_state_not_chat_code(client, app_db):
    _setup_channel_codes_project(app_db)
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "advance"})  # now in state "b" (chat: false)

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200
    assert parse_chat_turn_sse_error(response)["code"] == "state_not_chat"


@pytest.mark.contract
def test_chat_turn_exposes_session_channel_mismatch_code(client, app_db):
    _setup_channel_codes_project(app_db)
    session = client.get("/api/chat/session").json()

    Session().channel = WHATSAPP_CHAT
    try:
        response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})
    finally:
        Session().channel = NATIVE_CHAT

    assert response.status_code == 200
    assert parse_chat_turn_sse_error(response)["code"] == "session_channel_mismatch"


@pytest.mark.contract
def test_chat_turn_exposes_session_superseded_code(client, app_db):
    _setup_channel_codes_project(app_db)
    older = client.get("/api/chat/session").json()
    # A second live session appearing outside ChatService's own
    # close-before-create flow (e.g. an import) — `older` is still open,
    # just no longer the active one.
    app_db.create_chat_session(
        "user", "channel-codes-proj", app_db.get_project_published_revision("channel-codes-proj"),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a", type="live", channel=NATIVE_CHAT,
    )

    response = client.post(f"/api/chat/sessions/{older['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200
    assert parse_chat_turn_sse_error(response)["code"] == "session_superseded"


async def test_manual_action_exposes_turn_in_progress_code(client, app_db):
    _setup_channel_codes_project(app_db)
    session = client.get("/api/chat/session").json()
    chat_service = client.app.state.chat_service
    lock = chat_service._session_locks.get(str(session["id"]))
    await lock.acquire()
    try:
        response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "advance"})
    finally:
        lock.release()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "turn_in_progress"


@pytest.mark.regression
def test_chat_turn_succeeds_against_the_active_session(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200


@pytest.mark.regression
def test_manual_action_rejects_a_session_closed_by_a_manual_new_session(client, hello_project):
    older = client.get("/api/chat/session").json()
    client.post("/api/chat/sessions")  # closes `older` (force-new-session) and supersedes it

    response = client.post(f"/api/chat/sessions/{older['id']}/action", json={"action_name": "chat"})

    assert response.status_code == 409
    body = response.json()
    assert "closed" in body["error"]["message"].lower()
    assert body["error"]["code"] == "session_closed"


@pytest.mark.regression
def test_chat_turn_rejects_a_closed_session_without_auto_rotating(client, hello_project, app_db: Db):
    session = client.get("/api/chat/session").json()
    stale_end = datetime.utcnow() - timedelta(hours=2)
    app_db.touch_chat_session(session["id"], stale_end, session["end_state"])

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200  # the turn endpoint always streams 200; failures arrive as an SSE `error` event
    parse_chat_turn_sse_error(response)
    # No silent rotation: nothing new was created on the closed session's behalf.
    sessions = client.get(f"/api/projects/{hello_project}/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]


@pytest.mark.regression
def test_delete_session_removes_it(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.delete(f"/api/chat/sessions/{session['id']}")
    assert response.status_code == 200

    assert client.get(f"/api/projects/{hello_project}/sessions").json() == []


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
    names = {}
    for key, sample in (("hello", "Hello world.zip"), ("cat", "Aprendr català.zip")):
        content = (samples_dir / sample).read_bytes()
        resp = client.put(f"/api/projects/{key}", content=content, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 200, resp.text
        names[key] = parse_sse_result(resp)["project_name"]
        resp = client.post(f"/api/projects/{names[key]}/publish", json={})
        assert resp.status_code == 200, resp.text

    client.put(f"/api/projects/{names['hello']}/activate")
    session = client.get("/api/chat/session").json()

    client.put(f"/api/projects/{names['cat']}/activate")

    # Switching the active project must never touch another project's sessions.
    assert app_db.get_chat_session(session["id"]) is not None


@pytest.mark.regression
def test_sessions_list_is_scoped_by_the_url_never_the_active_project(client):
    """GET /api/projects/{project_name}/sessions must return that exact
    project's own sessions regardless of which project is currently
    active."""
    samples_dir = Path(__file__).resolve().parent.parent / "samples" / "projects"
    names = {}
    for key, sample in (("hello", "Hello world.zip"), ("cat", "Aprendr català.zip")):
        content = (samples_dir / sample).read_bytes()
        resp = client.put(f"/api/projects/{key}", content=content, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 200, resp.text
        names[key] = parse_sse_result(resp)["project_name"]
        resp = client.post(f"/api/projects/{names[key]}/publish", json={})
        assert resp.status_code == 200, resp.text

    client.put(f"/api/projects/{names['hello']}/activate")
    hello_session = client.get("/api/chat/session").json()

    client.put(f"/api/projects/{names['cat']}/activate")

    # "cat" is now active, but the URL decides, not the active project.
    explicit_hello = client.get(f"/api/projects/{names['hello']}/sessions").json()
    assert [s["id"] for s in explicit_hello] == [hello_session["id"]]
    assert all(s["project_name"] == names["hello"] for s in explicit_hello)

    explicit_cat = client.get("/api/projects/cat/sessions").json()
    assert all(s["project_name"] == "cat" for s in explicit_cat)
