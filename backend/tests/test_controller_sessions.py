from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from chat.channels import NATIVE_CHAT, WHATSAPP_CHAT
from conftest import chat_turn, chat_turn_error
from conftest import parse_sse_result
from db import Db
from service_error import ServiceError
from session import Session

CHANNEL_CODES_PROJECT_YAML = """
project:
  id: channel_codes_proj
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

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _setup_channel_codes_project(app_db, project_name="channel-codes-proj"):
    app_db.ensure_project(project_name)
    app_db.save_project_files(
        project_name, {"index.yml": CHANNEL_CODES_PROJECT_YAML.encode("utf-8")}, {"index.yml": "text/yaml"},
    )
    app_db.publish_project(project_name)
    app_db.set_active_project_id(project_name, "user")


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


def _import_session(client, project_id) -> int:
    imported = client.post(
        f"/api/projects/{project_id}/sessions/import", files=[("files", ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    return parse_sse_result(imported)["last_session_id"]


def _upload_and_publish(client, sample: str) -> str:
    content = (SAMPLES_DIR / sample).read_bytes()
    resp = client.post("/api/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def _sessions_by_id(client, project_id) -> dict:
    return {s["id"]: s for s in client.get(f"/api/projects/{project_id}/sessions").json()}


def _turn_error(client, session_id) -> dict:
    return chat_turn_error(client, session_id, "hi")


@pytest.mark.regression
def test_bootstrap_creates_a_session_whose_annotations_title_and_comment_round_trip_into_the_list(client, hello_project):
    """has_annotations reflects ChatSession.labeled directly, per session."""
    response = client.get("/api/chat/session")
    assert response.status_code == 200
    session = response.json()
    assert session["project_id"] == hello_project
    assert session["open"] is True
    assert session["active"] is True
    assert session["has_annotations"] is False
    assert session["comment"] is None
    assert _sessions_by_id(client, hello_project)[session["id"]]["has_annotations"] is False

    response = client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True
    assert _sessions_by_id(client, hello_project)[session["id"]]["has_annotations"] is True
    assert client.get(f"/api/chat/session?session_id={session['id']}").json()["has_annotations"] is True

    response = client.put(f"/api/chat/sessions/{session['id']}/title", json={"title": "My session"})
    assert response.status_code == 200
    assert response.json()["title"] == "My session"
    assert _sessions_by_id(client, hello_project)[session["id"]]["title"] == "My session"
    assert client.put(f"/api/chat/sessions/{session['id']}/title", json={"title": "   "}).json()["title"] is None

    response = client.put(f"/api/chat/sessions/{session['id']}/comment", json={"comment": "Worth a second look."})
    assert response.status_code == 200
    assert response.json()["comment"] == "Worth a second look."
    assert client.put(f"/api/chat/sessions/{session['id']}/comment", json={"comment": None}).json()["comment"] is None


@pytest.mark.regression
def test_labeled_title_and_comment_work_for_an_imported_session_which_is_never_active(client, hello_project):
    """An imported session always has datetime_end=None, which must not
    crash is_open's active-session check on the shared active-resolution
    path these updates use."""
    session_id = _import_session(client, hello_project)

    response = client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True
    assert response.json()["active"] is False
    assert client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": False}).json()["has_annotations"] is False

    title_resp = client.put(f"/api/chat/sessions/{session_id}/title", json={"title": "Renamed import"})
    assert title_resp.status_code == 200
    assert title_resp.json()["active"] is False

    comment_resp = client.put(f"/api/chat/sessions/{session_id}/comment", json={"comment": "note"})
    assert comment_resp.status_code == 200
    assert comment_resp.json()["comment"] == "note"


@pytest.mark.contract
def test_title_close_and_delete_reject_an_unknown_session(client, hello_project):
    assert client.put("/api/chat/sessions/999999/title", json={"title": "x"}).status_code == 404
    assert client.post("/api/chat/sessions/999999/close").status_code == 404
    assert client.delete("/api/chat/sessions/999999").status_code == 404


@pytest.mark.regression
def test_a_manual_new_session_closes_and_supersedes_the_bootstrap_one_rejecting_turns_and_actions_on_it(client, hello_project):
    older = client.get("/api/chat/session").json()

    newer = client.post("/api/chat/sessions").json()

    assert newer["id"] != older["id"]
    assert newer["active"] is True
    sessions = _sessions_by_id(client, hello_project)
    # "New session" explicitly closes whatever was open before creating
    # the new one — the older session is closed, not just superseded.
    assert sessions[older["id"]]["open"] is False
    assert sessions[older["id"]]["active"] is False
    assert sessions[older["id"]]["close_reason"] == "force-new-session"
    assert sessions[newer["id"]]["open"] is True
    assert sessions[newer["id"]]["active"] is True

    error = _turn_error(client, older["id"])
    assert "closed" in error["message"].lower()
    assert error["code"] == "session_closed"

    response = client.post(f"/api/chat/sessions/{older['id']}/action", json={"action_name": "chat"})
    assert response.status_code == 409
    assert "closed" in response.json()["error"]["message"].lower()
    assert response.json()["error"]["code"] == "session_closed"


@pytest.mark.regression
def test_close_session_ends_it_idempotently_without_a_replacement_and_turns_on_it_are_rejected(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.post(f"/api/chat/sessions/{session['id']}/close")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session["id"]
    assert body["active"] is False
    assert body["open"] is False
    assert body["close_reason"] == "manual-user"
    sessions = _sessions_by_id(client, hello_project)
    assert set(sessions) == {session["id"]}
    assert sessions[session["id"]]["active"] is False
    assert sessions[session["id"]]["open"] is False

    again = client.post(f"/api/chat/sessions/{session['id']}/close")
    assert again.status_code == 200
    assert again.json()["active"] is False

    assert _turn_error(client, session["id"])["code"] == "session_closed"


@pytest.mark.contract
def test_someone_elses_session_exposes_session_not_found_on_turns_and_actions(client, app_db):
    _setup_channel_codes_project(app_db)
    session_id = _someone_elses_session(app_db)

    assert _turn_error(client, session_id)["code"] == "session_not_found"

    response = client.post(f"/api/chat/sessions/{session_id}/action", json={"action_name": "advance"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.contract
def test_a_turn_in_a_non_chat_state_exposes_state_not_chat(client, app_db):
    _setup_channel_codes_project(app_db)
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "advance"})

    assert _turn_error(client, session["id"])["code"] == "state_not_chat"


@pytest.mark.contract
def test_a_turn_from_another_channel_or_on_a_superseded_session_exposes_the_matching_code(client, app_db):
    _setup_channel_codes_project(app_db)
    older = client.get("/api/chat/session").json()

    # The websocket is the native chat by definition — a turn from another
    # channel only ever reaches ChatService.process_turn directly, the
    # way WhatsAppService does.
    Session().channel = WHATSAPP_CHAT
    try:
        with pytest.raises(ServiceError) as raised:
            asyncio.run(client.app.state.chat_service.process_turn(older["id"], "hi"))
    finally:
        Session().channel = NATIVE_CHAT
    assert raised.value.code == "session_channel_mismatch"

    # A second live session appearing outside ChatService's own
    # close-before-create flow (e.g. an import) — `older` is still open,
    # just no longer the active one.
    app_db.create_chat_session(
        "user", "channel-codes-proj", app_db.get_project_published_revision("channel-codes-proj"),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a", type="live", channel=NATIVE_CHAT,
    )
    assert _turn_error(client, older["id"])["code"] == "session_superseded"


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
def test_a_turn_succeeds_against_the_active_session_and_delete_removes_it(client, hello_project):
    session = client.get("/api/chat/session").json()

    assert chat_turn(client, session['id'], "hi")["session_id"] == session["id"]

    assert client.delete(f"/api/chat/sessions/{session['id']}").status_code == 200
    assert client.get(f"/api/projects/{hello_project}/sessions").json() == []


@pytest.mark.regression
def test_a_turn_rejects_an_idle_session_without_auto_rotating(client, hello_project, app_db: Db):
    session = client.get("/api/chat/session").json()
    stale_end = datetime.utcnow() - timedelta(hours=2)
    app_db.touch_chat_session(session["id"], stale_end, session["end_state"])

    _turn_error(client, session["id"])

    # No silent rotation: nothing new was created on the closed session's behalf.
    sessions = client.get(f"/api/projects/{hello_project}/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]


@pytest.mark.regression
def test_manual_new_session_starts_at_the_automatons_current_state_not_the_initial_one(client):
    """A brand new session must start wherever the project's shared
    automaton position currently sits, never silently rewound to
    init_action.target."""
    project_id = _upload_and_publish(client, "Aprendr català.zip")
    client.put(f"/api/projects/{project_id}/activate")

    bootstrap = client.get("/api/chat/session").json()
    assert bootstrap["start_state"] == "welcome"

    action_response = client.post(f"/api/chat/sessions/{bootstrap['id']}/action", json={"action_name": "unit-subjuntive"})
    assert action_response.status_code == 200
    current_state = action_response.json()["state"]["key"]
    assert current_state != "welcome"

    assert client.post("/api/chat/sessions").json()["start_state"] == current_state


@pytest.mark.regression
def test_switching_the_active_project_keeps_the_other_projects_sessions_and_the_list_is_scoped_by_the_url(client, app_db: Db):
    """GET /api/projects/{project_name}/sessions must return that exact
    project's own sessions regardless of which project is currently
    active, and switching must never touch another project's sessions."""
    hello = _upload_and_publish(client, "Hello world.zip")
    cat = _upload_and_publish(client, "Aprendr català.zip")

    client.put(f"/api/projects/{hello}/activate")
    hello_session = client.get("/api/chat/session").json()

    client.put(f"/api/projects/{cat}/activate")

    assert app_db.get_chat_session(hello_session["id"]) is not None
    explicit_hello = client.get(f"/api/projects/{hello}/sessions").json()
    assert [s["id"] for s in explicit_hello] == [hello_session["id"]]
    assert all(s["project_id"] == hello for s in explicit_hello)
    assert all(s["project_id"] == cat for s in client.get(f"/api/projects/{cat}/sessions").json())
