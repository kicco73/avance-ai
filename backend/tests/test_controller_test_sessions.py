"""Regression coverage for the dedicated draft-session entry points: only
POST /api/projects/{project_name}/test-sessions and GET .../current may
create a session against an unpublished revision — every other entry
point requires a published one, unconditionally.
"""
from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.regression

UNPUBLISHED_PROJECT = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload_and_activate(client, name: str, yaml_text: str):
    response = client.put(
        f"/api/projects/{name}", content=_zip_of(yaml_text), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    response = client.put(f"/api/projects/{name}/activate")
    assert response.status_code == 200, response.text


def test_regular_session_bootstrap_fails_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-1", UNPUBLISHED_PROJECT)

    response = client.get("/api/chat/session")

    assert response.status_code == 409
    assert "never been published" in response.json()["error"]["message"]


def test_regular_session_creation_fails_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-2", UNPUBLISHED_PROJECT)

    response = client.post("/api/chat/sessions")

    assert response.status_code == 409
    assert "never been published" in response.json()["error"]["message"]


def test_test_session_bootstrap_succeeds_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-3", UNPUBLISHED_PROJECT)

    response = client.get("/api/projects/draft-only-3/test-sessions/current")

    assert response.status_code == 200
    body = response.json()
    assert body["project_name"] == "draft-only-3"
    assert body["active"] is True


def test_post_test_session_succeeds_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-4", UNPUBLISHED_PROJECT)

    response = client.post("/api/projects/draft-only-4/test-sessions")

    assert response.status_code == 200
    body = response.json()
    assert body["project_name"] == "draft-only-4"


def test_allow_draft_query_param_no_longer_has_any_effect(client):
    """A caller cannot opt into a draft session from the shared endpoint
    via a query param — the choice is solely which endpoint is called."""
    _upload_and_activate(client, "draft-only-5", UNPUBLISHED_PROJECT)

    response = client.get("/api/chat/session?allow_draft=true")

    assert response.status_code == 409


def _publish(client, name: str) -> None:
    response = client.post(f"/api/projects/{name}/publish", json={})
    assert response.status_code == 200, response.text


def test_a_test_session_never_appears_in_the_regular_sessions_list(client):
    _upload_and_activate(client, "isolation-1", UNPUBLISHED_PROJECT)
    _publish(client, "isolation-1")
    test_session = client.post("/api/projects/isolation-1/test-sessions").json()

    body = client.get("/api/projects/isolation-1/sessions").json()

    assert test_session["id"] not in [s["id"] for s in body]


def test_a_native_session_never_appears_in_the_test_sessions_list(client):
    _upload_and_activate(client, "isolation-2", UNPUBLISHED_PROJECT)
    _publish(client, "isolation-2")
    native_session = client.get("/api/chat/session").json()

    body = client.get("/api/projects/isolation-2/test-sessions").json()

    assert native_session["id"] not in [s["id"] for s in body]


def test_regular_bootstrap_and_test_bootstrap_never_resolve_to_the_same_session(client):
    _upload_and_activate(client, "isolation-3", UNPUBLISHED_PROJECT)
    _publish(client, "isolation-3")

    native_session = client.get("/api/chat/session").json()
    test_session = client.get("/api/projects/isolation-3/test-sessions/current").json()

    assert native_session["id"] != test_session["id"]
    # Each is "active" only within its own pool.
    assert native_session["active"] is True
    assert test_session["active"] is True


def test_a_chat_turn_against_a_test_session_is_accepted_as_active(client):
    """A 'test' session must be usable for real turns/manual actions from
    within the Test chat, just never visible/active outside it."""
    _upload_and_activate(client, "isolation-4", UNPUBLISHED_PROJECT)
    _publish(client, "isolation-4")
    test_session = client.post("/api/projects/isolation-4/test-sessions").json()

    response = client.post(f"/api/chat/sessions/{test_session['id']}/messages", json={"message": "hi"})

    assert response.status_code == 200


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
    _upload_and_activate(client, "test-session-sees-live-draft", PROJECT_WITH_A_SELF_LOOP)
    _publish(client, "test-session-sees-live-draft")
    test_session = client.post("/api/projects/test-session-sees-live-draft/test-sessions").json()
    # Bootstraps the session's opening turn so the project has a real
    # current_state before the draft edit below.
    assert client.get(f"/api/chat/sessions/{test_session['id']}/messages").status_code == 200

    # Edits the draft after the test session above already exists.
    new_action = client.post(
        "/api/projects/test-session-sees-live-draft/states/a/actions"
    ).json()

    response = client.post(f"/api/chat/sessions/{test_session['id']}/action", json={"action_name": new_action["name"]})

    assert response.status_code == 200
