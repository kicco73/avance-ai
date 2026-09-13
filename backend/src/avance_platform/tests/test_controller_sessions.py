from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import pytest

from conftest import (
    _frame_deadline, chat_action, chat_action_error, chat_socket, chat_turn, chat_turn_error,
    enter_chat, session_of,
    turn_frame_seconds,
)
from conftest import parse_sse_result
from db import Db
from system.service_error import ServiceError
from system.web_session import WebSession

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
    chat-enabled: false
"""

from conftest import SAMPLES_DIR


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
        start_state="a", end_state="a", type="live", channel="webchat",
    )


def _import_session(client, project_id) -> int:
    imported = client.post(
        f"/api/skills/platform/projects/{project_id}/sessions/import", files=[("files", ("transcript.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    return parse_sse_result(imported)["last_session_id"]


def _upload_and_publish(client, sample: str) -> str:
    content = (SAMPLES_DIR / sample).read_bytes()
    resp = client.post("/api/skills/platform/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def _sessions_by_id(client, project_id) -> dict:
    return {s["id"]: s for s in client.get(f"/api/core/projects/{project_id}/sessions").json()}


def _turn_error(client, session_id) -> dict:
    return chat_turn_error(client, session_id, "hi")


def _new_chat(client, project_id) -> list[dict]:
    frames = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.create", "project_id": project_id, "session_type": "live"})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] in ("state.buttons", "session.blocked"):
                    return frames


def _terminate_then_speak(client, session_id) -> dict:
    frames = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.terminate", "session_id": session_id})
            ws.send_json({"type": "input.text", "session_id": session_id, "text": "hi"})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] == "output.error":
                    return frames[-1]


@pytest.mark.regression
def test_bootstrap_creates_a_session_whose_annotations_title_and_comment_round_trip_into_the_list(client, hello_project):
    """has_annotations reflects CoreSession.labeled directly, per session."""
    frames = enter_chat(client, hello_project)
    session_id = session_of(frames)
    info = next(frame for frame in frames if frame["type"] == "session.info")
    assert info["project_id"] == hello_project
    assert info["current"] is True
    session = _sessions_by_id(client, hello_project)[session_id]
    assert session["open"] is True
    assert session["current"] is True
    assert session["has_annotations"] is False
    assert session["comment"] is None

    response = client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True
    assert _sessions_by_id(client, hello_project)[session_id]["has_annotations"] is True

    response = client.put(f"/api/skills/platform/sessions/{session_id}/title", json={"title": "My session"})
    assert response.status_code == 200
    assert response.json()["title"] == "My session"
    assert _sessions_by_id(client, hello_project)[session_id]["title"] == "My session"
    assert client.put(f"/api/skills/platform/sessions/{session_id}/title", json={"title": "   "}).json()["title"] is None

    response = client.put(f"/api/skills/platform/sessions/{session_id}/comment", json={"comment": "Worth a second look."})
    assert response.status_code == 200
    assert response.json()["comment"] == "Worth a second look."
    assert client.put(f"/api/skills/platform/sessions/{session_id}/comment", json={"comment": None}).json()["comment"] is None


@pytest.mark.regression
def test_labeled_title_and_comment_work_for_an_imported_session_which_is_never_active(client, hello_project):
    """An imported session always has datetime_end=None, which must not
    crash is_open's active-session check on the shared active-resolution
    path these updates use."""
    session_id = _import_session(client, hello_project)

    response = client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": True})
    assert response.status_code == 200
    assert response.json()["has_annotations"] is True
    assert response.json()["current"] is False
    assert client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": False}).json()["has_annotations"] is False

    title_resp = client.put(f"/api/skills/platform/sessions/{session_id}/title", json={"title": "Renamed import"})
    assert title_resp.status_code == 200
    assert title_resp.json()["current"] is False

    comment_resp = client.put(f"/api/skills/platform/sessions/{session_id}/comment", json={"comment": "note"})
    assert comment_resp.status_code == 200
    assert comment_resp.json()["comment"] == "note"


@pytest.mark.contract
def test_title_and_delete_reject_an_unknown_session(client):
    assert client.put("/api/skills/platform/sessions/999999/title", json={"title": "x"}).status_code == 404
    assert client.delete("/api/core/sessions/999999").status_code == 404


@pytest.mark.regression
def test_a_manual_new_session_closes_and_supersedes_the_bootstrap_one_rejecting_turns_and_actions_on_it(client, hello_project):
    older_id = session_of(enter_chat(client, hello_project))

    newer = next(frame for frame in _new_chat(client, hello_project) if frame["type"] == "session.info")

    assert newer["session_id"] != older_id
    assert newer["current"] is True
    sessions = _sessions_by_id(client, hello_project)
    # "New session" explicitly closes whatever was open before creating
    # the new one — the older session is closed, not just superseded.
    assert sessions[older_id]["open"] is False
    assert sessions[older_id]["current"] is False
    assert sessions[older_id]["close_reason"] == "force-new-session"
    assert sessions[newer["session_id"]]["open"] is True
    assert sessions[newer["session_id"]]["current"] is True

    error = _turn_error(client, older_id)
    assert "closed" in error["message"].lower()
    assert error["code"] == "session_closed"

    refused = chat_action_error(client, older_id, "chat")
    assert "closed" in refused["message"].lower()
    assert refused["code"] == "session_closed"


@pytest.mark.regression
def test_terminate_ends_it_idempotently_without_a_replacement_and_turns_on_it_are_rejected(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))

    assert _terminate_then_speak(client, session_id)["code"] == "session_closed"

    sessions = _sessions_by_id(client, hello_project)
    assert set(sessions) == {session_id}
    assert sessions[session_id]["current"] is False
    assert sessions[session_id]["open"] is False
    assert sessions[session_id]["close_reason"] == "manual-user"

    assert _terminate_then_speak(client, session_id)["code"] == "session_closed"
    assert set(_sessions_by_id(client, hello_project)) == {session_id}


@pytest.mark.contract
def test_someone_elses_session_exposes_session_not_found_on_turns_and_actions(client, app_db):
    _setup_channel_codes_project(app_db)
    session_id = _someone_elses_session(app_db)

    assert _turn_error(client, session_id)["code"] == "session_not_found"

    assert chat_action_error(client, session_id, "advance")["code"] == "session_not_found"


@pytest.mark.contract
def test_a_turn_in_a_non_chat_state_exposes_state_not_chat(client, app_db):
    _setup_channel_codes_project(app_db)
    session_id = session_of(enter_chat(client, "channel-codes-proj"))
    chat_action(client, session_id, "advance")

    assert _turn_error(client, session_id)["code"] == "state_not_chat"


@pytest.mark.contract
def test_a_turn_from_another_channel_or_on_a_superseded_session_exposes_the_matching_code(client, app_db):
    _setup_channel_codes_project(app_db)
    older_id = session_of(enter_chat(client, "channel-codes-proj"))

    # The websocket is the native chat by definition — a turn from another
    # channel only ever reaches TurnService.process_turn directly, the
    # way WhatsAppService does.
    WebSession().channel = "whatsapp"
    try:
        with pytest.raises(ServiceError) as raised:
            asyncio.run(client.app.state.turn_service.process_turn(older_id, "hi"))
    finally:
        WebSession().channel = "webchat"
    assert raised.value.code == "session_channel_mismatch"

    # A second live session appearing outside TurnService's own
    # close-before-create flow (e.g. an import) — `older` is still open,
    # just no longer the active one.
    app_db.create_chat_session(
        "user", "channel-codes-proj", app_db.get_project_published_revision("channel-codes-proj"),
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a", type="live", channel="webchat",
    )
    assert _turn_error(client, older_id)["code"] == "session_superseded"


async def test_manual_action_exposes_turn_in_progress_code(client, app_db):
    _setup_channel_codes_project(app_db)
    session_id = session_of(enter_chat(client, "channel-codes-proj"))
    turn_service = client.app.state.turn_service
    lock = turn_service._session_locks.get(str(session_id))
    await lock.acquire()
    try:
        refused = chat_action_error(client, session_id, "advance")
    finally:
        lock.release()

    assert refused["code"] == "turn_in_progress"


@pytest.mark.regression
def test_a_turn_succeeds_against_the_active_session_and_delete_removes_it(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))

    assert chat_turn(client, session_id, "hi")["session_id"] == session_id

    assert client.delete(f"/api/core/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/core/projects/{hello_project}/sessions").json() == []


@pytest.mark.regression
def test_a_turn_rejects_an_idle_session_without_auto_rotating(client, hello_project, app_db: Db):
    session_id = session_of(enter_chat(client, hello_project))
    stale_end = datetime.utcnow() - timedelta(hours=2)
    app_db.touch_chat_session(session_id, stale_end, app_db.get_chat_session(session_id)["end_state"])

    _turn_error(client, session_id)

    # No silent rotation: nothing new was created on the closed session's behalf.
    sessions = client.get(f"/api/core/projects/{hello_project}/sessions").json()
    assert [s["id"] for s in sessions] == [session_id]


@pytest.mark.regression
def test_manual_new_session_starts_at_the_automatons_current_state_not_the_initial_one(client):
    """A brand new session must start wherever the project's shared
    automaton position currently sits, never silently rewound to
    init_action.target."""
    project_id = _upload_and_publish(client, "Aprendr català.zip")
    client.post(f"/api/skills/platform/projects/{project_id}/activate")

    bootstrap_id = session_of(enter_chat(client, project_id))
    assert _sessions_by_id(client, project_id)[bootstrap_id]["start_state"] == "welcome"

    current_state = chat_action(client, bootstrap_id, "unit-subjuntive")["state"]["key"]
    assert current_state != "welcome"

    new_id = session_of(_new_chat(client, project_id))
    assert _sessions_by_id(client, project_id)[new_id]["start_state"] == current_state


@pytest.mark.regression
def test_switching_the_active_project_keeps_the_other_projects_sessions_and_the_list_is_scoped_by_the_url(client, app_db: Db):
    """GET /api/core/projects/{project_name}/sessions must return that exact
    project's own sessions regardless of which project is currently
    active, and switching must never touch another project's sessions."""
    hello = _upload_and_publish(client, "Hello world.zip")
    cat = _upload_and_publish(client, "Aprendr català.zip")

    client.post(f"/api/skills/platform/projects/{hello}/activate")
    hello_session_id = session_of(enter_chat(client, hello))

    client.post(f"/api/skills/platform/projects/{cat}/activate")

    assert app_db.get_chat_session(hello_session_id) is not None
    explicit_hello = client.get(f"/api/core/projects/{hello}/sessions").json()
    assert [s["id"] for s in explicit_hello] == [hello_session_id]
    assert all(s["project_id"] == hello for s in explicit_hello)
    assert all(s["project_id"] == cat for s in client.get(f"/api/core/projects/{cat}/sessions").json())
