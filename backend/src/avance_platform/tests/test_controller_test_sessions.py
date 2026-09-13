"""Regression coverage for the dedicated draft-session entry points: only
POST /api/skills/platform/projects/{project_id}/test-sessions and GET .../current may
create a session against an unpublished revision — entering a live
conversation requires a published one, unconditionally.
"""
from __future__ import annotations

import contextvars

import pytest

from conftest import (
    _frame_deadline, chat_socket, chat_turn, enter_chat, parse_sse_result, session_of,
    turn_frame_seconds,
)
from system.web_session import WebSession

pytestmark = pytest.mark.regression

UNPUBLISHED_PROJECT = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def _enter_frames(client, project_id: str, kind: str = "live", type: str = "session.enter") -> list[dict]:
    frames = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": type, "project_id": project_id, "session_type": kind})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] in ("state.buttons", "session.blocked", "output.error"):
                    return frames


def _upload_and_activate(client, project_id: str, yaml_text: str) -> str:
    full_yaml = f"project:\n  id: {project_id}\n" + yaml_text
    response = client.post(
        "/api/skills/platform/projects/upload", content=full_yaml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    returned_id = parse_sse_result(response)["project_id"]
    assert returned_id == project_id
    response = client.post(f"/api/skills/platform/projects/{returned_id}/activate")
    assert response.status_code == 200, response.text
    return returned_id


def _setup_unpublished_project(app_db, project_id: str, yaml_text: str) -> None:
    """Creates `project_id`'s draft straight at the Db layer, deliberately
    never publishing it — POST /api/skills/platform/projects/upload always publishes on
    the way in now (see ProjectManager.put_project), which is exactly the
    state these tests need to avoid."""
    full_yaml = f"project:\n  id: {project_id}\n" + yaml_text
    app_db.ensure_project(project_id)
    app_db.save_project_files(project_id, {"index.yml": full_yaml.encode("utf-8")}, {"index.yml": "text/yaml"})
    app_db.set_active_project_id(project_id, "user")


def test_regular_session_bootstrap_fails_for_an_unpublished_project(client, app_db):
    _setup_unpublished_project(app_db, "draft_only_1", UNPUBLISHED_PROJECT)

    refusal = _enter_frames(client, "draft_only_1")[-1]

    assert refusal["type"] == "output.error"
    assert "never been published" in refusal["message"]


def test_regular_session_creation_fails_for_an_unpublished_project(client, app_db):
    _setup_unpublished_project(app_db, "draft_only_2", UNPUBLISHED_PROJECT)

    refusal = _enter_frames(client, "draft_only_2", type="session.create")[-1]

    assert refusal["type"] == "output.error"
    assert "never been published" in refusal["message"]


def test_test_session_bootstrap_succeeds_for_an_unpublished_project(client, app_db):
    _setup_unpublished_project(app_db, "draft_only_3", UNPUBLISHED_PROJECT)

    response = client.get("/api/skills/platform/projects/draft_only_3/test-sessions/current")

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "draft_only_3"
    assert body["current"] is True


def test_current_test_session_resumes_the_most_recent_one_instead_of_creating_a_new_one(client):
    """Regression: EditProjectView's own mode-switch flow (Design -> Run ->
    Design -> Run) always resolves current-test-session with no session_id
    of its own — this must resume the existing draft, never spawn a new
    one each time the tab is re-entered."""
    _upload_and_activate(client, "resume_1", UNPUBLISHED_PROJECT)
    _publish(client, "resume_1")
    first = client.get("/api/skills/platform/projects/resume_1/test-sessions/current").json()

    second = client.get("/api/skills/platform/projects/resume_1/test-sessions/current").json()

    assert second["id"] == first["id"]


def test_current_test_session_still_creates_one_when_none_exists(client):
    _upload_and_activate(client, "resume_2", UNPUBLISHED_PROJECT)
    _publish(client, "resume_2")

    response = client.get("/api/skills/platform/projects/resume_2/test-sessions/current")

    assert response.status_code == 200
    assert response.json()["id"] is not None


def test_post_test_session_succeeds_for_an_unpublished_project(client, app_db):
    _setup_unpublished_project(app_db, "draft_only_4", UNPUBLISHED_PROJECT)

    response = client.post("/api/skills/platform/projects/draft_only_4/test-sessions")

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "draft_only_4"


def _publish(client, project_id: str) -> None:
    response = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text


def test_a_test_session_never_appears_in_the_regular_sessions_list(client):
    _upload_and_activate(client, "isolation_1", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_1")
    test_session = client.post("/api/skills/platform/projects/isolation_1/test-sessions").json()

    body = client.get("/api/core/projects/isolation_1/sessions").json()

    assert test_session["id"] not in [s["id"] for s in body]


def test_a_native_session_never_appears_in_the_test_sessions_list(client):
    _upload_and_activate(client, "isolation_2", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_2")
    native_session_id = session_of(enter_chat(client, "isolation_2"))

    body = client.get("/api/skills/platform/projects/isolation_2/test-sessions").json()

    assert native_session_id not in [s["id"] for s in body]


def test_regular_bootstrap_and_test_bootstrap_never_resolve_to_the_same_session(client):
    _upload_and_activate(client, "isolation_3", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_3")

    native = next(
        frame for frame in enter_chat(client, "isolation_3") if frame["type"] == "session.info"
    )
    test_session = client.get("/api/skills/platform/projects/isolation_3/test-sessions/current").json()

    assert native["session_id"] != test_session["id"]
    # Each is "current" only within its own pool.
    assert native["current"] is True
    assert test_session["current"] is True


def test_every_test_session_is_reported_current_not_just_the_most_recent(client):
    _upload_and_activate(client, "isolation_5", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_5")
    first = client.post("/api/skills/platform/projects/isolation_5/test-sessions").json()
    second = client.post("/api/skills/platform/projects/isolation_5/test-sessions").json()

    body = client.get("/api/skills/platform/projects/isolation_5/test-sessions").json()

    by_id = {s["id"]: s for s in body}
    assert by_id[first["id"]]["current"] is True
    assert by_id[second["id"]]["current"] is True


def test_a_chat_turn_against_a_test_session_is_accepted_as_active(client):
    """A 'test' session must be usable for real turns/manual actions from
    within the Test chat, just never visible/active outside it."""
    _upload_and_activate(client, "isolation_4", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_4")
    test_session = client.post("/api/skills/platform/projects/isolation_4/test-sessions").json()

    turn = chat_turn(client, test_session['id'], "hi")

    assert turn["session_id"] == test_session["id"]


PROJECT_WITH_A_SELF_LOOP = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: stay
        target: a
"""


def test_a_turn_against_a_test_session_sees_a_draft_edit_made_after_it_was_created(client):
    """A 'test' session must re-resolve every turn against the draft as it
    looks right now, not as it looked when the session was bootstrapped —
    unlike a native session, which stays pinned to its project_revision."""
    _upload_and_activate(client, "test_session_sees_live_draft", PROJECT_WITH_A_SELF_LOOP)
    _publish(client, "test_session_sees_live_draft")
    test_session = client.post("/api/skills/platform/projects/test_session_sees_live_draft/test-sessions").json()
    # Bootstraps the session's opening turn so the project has a real
    # current_state before the draft edit below.
    assert session_of(enter_chat(client, "test_session_sees_live_draft", "test")) == test_session["id"]

    # Edits the draft after the test session above already exists.
    new_action = client.post(
        "/api/skills/platform/projects/test_session_sees_live_draft/states/a/actions"
    ).json()

    response = client.post(f"/api/core/sessions/{test_session['id']}/actions", json={"action_name": new_action["name"]})

    assert response.status_code == 200


def test_a_test_session_opened_from_the_editor_has_no_channel(client):
    """The editor is not a channel and has none to declare. It used to
    get native-chat anyway, from AuthMiddleware, so every test session
    claimed to have been opened from the chat window. Run in a context of
    its own so the suite's own WebSession().channel default cannot supply
    what the route no longer does."""
    _upload_and_activate(client, "no_channel_1", UNPUBLISHED_PROJECT)

    def open_one():
        WebSession().user = "user"
        WebSession().role = "supervisor"
        return client.post("/api/skills/platform/projects/no_channel_1/test-sessions").json()

    session = contextvars.Context().run(open_one)

    assert session["type"] == "test"
    assert session["channel"] is None
