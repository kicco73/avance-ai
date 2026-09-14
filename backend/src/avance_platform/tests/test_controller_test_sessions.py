"""Regression coverage for the one entry point that may open a session
against an unpublished revision: `session.enter`/`session.create` with
`session_type: test`. Entering a live conversation requires a published
revision, unconditionally.

The two HTTP routes this file used to drive (POST .../test-sessions and
GET .../current) were the pre-Bus version of the same thing and are gone;
what they guarded is asked of the Bus here.
"""
from __future__ import annotations

import contextvars

import pytest

from conftest import (
    _frame_deadline, chat_action, chat_socket, chat_turn, create_chat, enter_chat, parse_sse_result,
    session_of, turn_frame_seconds,
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
    actions:
      - name: stay
        target: a
        trigger: "False"
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


def _info(frames: list[dict]) -> dict:
    return next(frame for frame in frames if frame["type"] == "session.info")


def _upload_and_activate(client, project_id: str, yaml_text: str) -> str:
    full_yaml = f"project:\n  id: {project_id}\n" + yaml_text
    response = client.post(
        "/api/skills/platform/projects/upload", content=full_yaml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    returned_id = parse_sse_result(response)["project_id"]
    assert returned_id == project_id
    response = client.post(f"/api/core/projects/{returned_id}/activate")
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

    info = _info(enter_chat(client, "draft_only_3", "test"))

    assert info["project_id"] == "draft_only_3"
    assert info["current"] is True


def test_entering_the_test_chat_resumes_the_most_recent_one_instead_of_creating_a_new_one(client):
    """Regression: EditProjectView's own mode-switch flow (Design -> Run ->
    Design -> Run) enters with no session_id of its own — this must resume
    the existing draft, never spawn a new one each time the tab is
    re-entered."""
    _upload_and_activate(client, "resume_1", UNPUBLISHED_PROJECT)
    _publish(client, "resume_1")
    first = session_of(enter_chat(client, "resume_1", "test"))

    second = session_of(enter_chat(client, "resume_1", "test"))

    assert second == first


def test_entering_the_test_chat_still_creates_one_when_none_exists(client):
    _upload_and_activate(client, "resume_2", UNPUBLISHED_PROJECT)
    _publish(client, "resume_2")

    assert session_of(enter_chat(client, "resume_2", "test")) is not None


def test_creating_a_test_session_succeeds_for_an_unpublished_project(client, app_db):
    _setup_unpublished_project(app_db, "draft_only_4", UNPUBLISHED_PROJECT)

    info = _info(create_chat(client, "draft_only_4", "test"))

    assert info["project_id"] == "draft_only_4"


def _publish(client, project_id: str) -> None:
    response = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text


def test_a_test_session_never_appears_in_the_regular_sessions_list(client):
    _upload_and_activate(client, "isolation_1", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_1")
    test_session_id = session_of(create_chat(client, "isolation_1", "test"))

    body = client.get("/api/core/projects/isolation_1/sessions").json()

    assert test_session_id not in [s["id"] for s in body]


def test_a_native_session_never_appears_in_the_test_sessions_list(client):
    _upload_and_activate(client, "isolation_2", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_2")
    native_session_id = session_of(enter_chat(client, "isolation_2"))

    body = client.get("/api/skills/platform/projects/isolation_2/test-sessions").json()

    assert native_session_id not in [s["id"] for s in body]


def test_regular_bootstrap_and_test_bootstrap_never_resolve_to_the_same_session(client):
    _upload_and_activate(client, "isolation_3", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_3")

    native = _info(enter_chat(client, "isolation_3"))
    test_session = _info(enter_chat(client, "isolation_3", "test"))

    assert native["session_id"] != test_session["session_id"]
    assert native["current"] is True
    assert test_session["current"] is True


def test_every_test_session_is_reported_current_not_just_the_most_recent(client):
    _upload_and_activate(client, "isolation_5", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_5")
    first = session_of(create_chat(client, "isolation_5", "test"))
    second = session_of(create_chat(client, "isolation_5", "test"))

    body = client.get("/api/skills/platform/projects/isolation_5/test-sessions").json()

    by_id = {s["id"]: s for s in body}
    assert by_id[first]["current"] is True
    assert by_id[second]["current"] is True


def test_a_chat_turn_against_a_test_session_is_accepted_as_active(client):
    """A 'test' session must be usable for real turns/manual actions from
    within the Test chat, just never visible/active outside it."""
    _upload_and_activate(client, "isolation_4", UNPUBLISHED_PROJECT)
    _publish(client, "isolation_4")
    test_session_id = session_of(create_chat(client, "isolation_4", "test"))

    turn = chat_turn(client, test_session_id, "hi")

    assert turn["session_id"] == test_session_id


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
    test_session_id = session_of(enter_chat(client, "test_session_sees_live_draft", "test"))

    new_action = client.post(
        "/api/skills/platform/projects/test_session_sees_live_draft/states/a/actions"
    ).json()

    chat_action(client, test_session_id, new_action["name"])


def test_a_test_session_opened_from_the_editor_has_no_channel(client):
    """The editor is not a channel and has none to declare. It used to
    get native-chat anyway, from AuthMiddleware, so every test session
    claimed to have been opened from the chat window. Run in a context of
    its own so the suite's own WebSession().channel default cannot supply
    what the session type no longer does."""
    _upload_and_activate(client, "no_channel_1", UNPUBLISHED_PROJECT)

    def open_one():
        WebSession().user = "user"
        WebSession().role = "supervisor"
        return _info(create_chat(client, "no_channel_1", "test"))

    info = contextvars.Context().run(open_one)

    assert info["session_type"] == "test"
    assert info["channel"] is None
