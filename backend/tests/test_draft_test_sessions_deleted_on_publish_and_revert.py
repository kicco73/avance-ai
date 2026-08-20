"""EditProjectView.vue's own embedded "Test" chat (source='test', see
ChatSessionManager.create_draft_session) is anchored to whichever draft
was current the moment it was created — but ChatSessionManager.
get_or_create_current_draft_session reuses an existing active one as-is,
"whatever revision it carried" (see its own docstring), and the automaton
a live turn actually runs against is always the *current* draft. Publish
or revert while a Test session is still open used to leave that stale
session silently anchored to a draft that no longer exists in that shape
— reopening Test would keep testing the wrong automaton. Db.
delete_draft_test_sessions closes this: every 'test' session is deleted
outright at both moments a Test session's own revision stops meaning
anything (publish, revert), so the next Test open always starts fresh
against whatever the draft actually is now. Deliberately not a revision-
number comparison (see that method's own docstring on why a coincidental
number match after a revert can't be trusted).
"""
from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.contract

TWO_STATE_YML = (
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload_activate_and_establish_state(client, project_name: str):
    """Uploads, activates, and publishes a two-state project, then fires
    its one real action against a native session — same reasoning as
    test_controller_project_graph_revision.py's own identically-purposed
    helper: _finalize_project_update's own "current_state can't be
    determined" cleanup (see ProjectService) wipes *every* session for the
    active project, test ones included, the next time it's edited/
    reverted — a concern entirely separate from (and easily mistaken for)
    delete_draft_test_sessions itself, sidestepped here by establishing a
    real current_state up front."""
    response = client.put(
        f"/api/projects/{project_name}",
        content=_zip_of({"index.yml": TWO_STATE_YML}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert client.put(f"/api/projects/{project_name}/activate").status_code == 200
    assert client.post(f"/api/projects/{project_name}/publish", json={}).status_code == 200

    session_response = client.get("/api/chat/session")
    assert session_response.status_code == 200, session_response.text
    action_response = client.post("/api/action", json={"action_name": "go", "session_id": session_response.json()["id"]})
    assert action_response.status_code == 200, action_response.text


def _create_test_session(client, project_name: str) -> int:
    response = client.post(f"/api/projects/{project_name}/test-sessions")
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _test_session_ids(client, project_name: str) -> set[int]:
    response = client.get(f"/api/projects/{project_name}/test-sessions")
    assert response.status_code == 200, response.text
    return {s["id"] for s in response.json()}


def test_publish_deletes_every_test_session(client):
    _upload_activate_and_establish_state(client, "proj")
    session_id = _create_test_session(client, "proj")
    assert session_id in _test_session_ids(client, "proj")

    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    assert _test_session_ids(client, "proj") == set()


def test_a_no_op_publish_still_deletes_test_sessions(client):
    """Deliberate — see Db.publish_project's own docstring: no `changed`
    guard, so even a double-click publish (nothing actually changed)
    clears stale Test sessions rather than risking one surviving under a
    coincidentally-matching revision number."""
    _upload_activate_and_establish_state(client, "proj")
    session_id = _create_test_session(client, "proj")

    resp = client.post("/api/projects/proj/publish", json={})
    assert resp.status_code == 200, resp.text

    assert session_id not in _test_session_ids(client, "proj")


def test_revert_deletes_every_test_session(client):
    _upload_activate_and_establish_state(client, "proj")
    client.put("/api/projects/proj/files/notes.txt", content=b"edited after publish")
    session_id = _create_test_session(client, "proj")
    assert session_id in _test_session_ids(client, "proj")

    assert client.post("/api/projects/proj/revert").status_code == 200

    assert _test_session_ids(client, "proj") == set()


def test_a_no_op_revert_does_not_delete_test_sessions(client):
    """revert_to_published's own early-return (nothing to revert to) never
    reaches delete_draft_test_sessions at all — a Test session created
    against an already-published, unedited draft is still perfectly
    valid, nothing about it went stale."""
    _upload_activate_and_establish_state(client, "proj")
    session_id = _create_test_session(client, "proj")

    resp = client.post("/api/projects/proj/revert")
    assert resp.status_code == 200, resp.text

    assert session_id in _test_session_ids(client, "proj")


def test_native_sessions_are_unaffected_by_publish(client):
    _upload_activate_and_establish_state(client, "proj")
    native_session_id = client.get("/api/chat/session").json()["id"]

    client.put("/api/projects/proj/files/notes.txt", content=b"edited")
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    sessions = client.get("/api/chat/sessions").json()
    assert any(s["id"] == native_session_id for s in sessions)


def test_imported_sessions_are_unaffected_by_publish(client):
    _upload_activate_and_establish_state(client, "proj")
    response = client.post(
        "/api/chat/sessions/import", files={"file": ("t.txt", "user: hi\nassistant: hello\n", "text/plain")}
    )
    assert response.status_code == 200, response.text
    imported_session_id = response.json()["session_id"]

    client.put("/api/projects/proj/files/notes.txt", content=b"edited")
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    sessions = client.get("/api/chat/sessions", params={"include_imported": True}).json()
    assert any(s["id"] == imported_session_id for s in sessions)
