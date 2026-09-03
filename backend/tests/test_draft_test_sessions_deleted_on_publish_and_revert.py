"""A 'test' session is anchored to whichever draft was current when it
was created. Db.delete_draft_test_sessions deletes every 'test' session
on publish and revert, so the next Test open starts against the current draft.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract

TWO_STATE_YML = (
    "project:\n  id: {project_id}\n"
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
    its one real action against a native session, establishing a real
    current_state before the test."""
    response = client.post(
        "/api/projects/upload",
        content=_zip_of({"index.yml": TWO_STATE_YML.format(project_id=project_name)}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == project_name
    assert client.put(f"/api/projects/{project_name}/activate").status_code == 200
    assert client.post(f"/api/projects/{project_name}/publish", json={}).status_code == 200

    session_response = client.get("/api/chat/session")
    assert session_response.status_code == 200, session_response.text
    action_response = client.post(f"/api/chat/sessions/{session_response.json()['id']}/action", json={"action_name": "go"})
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


def test_publish_does_not_delete_labeled_test_sessions(client):
    """Labeling freezes a 'test' session as durable benchmark ground
    truth — same contract as a labeled 'live' session, which already
    survives indefinitely. Test.session cascades on delete, so wiping a
    labeled session here would silently destroy every TestReplayJob
    result ever computed against it too, not just the session itself."""
    _upload_activate_and_establish_state(client, "proj")
    session_id = _create_test_session(client, "proj")
    assert client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True}).status_code == 200

    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    assert session_id in _test_session_ids(client, "proj")


def test_publish_still_deletes_unlabeled_test_sessions_alongside_a_labeled_one(client):
    _upload_activate_and_establish_state(client, "proj")
    labeled_id = _create_test_session(client, "proj")
    assert client.put(f"/api/chat/sessions/{labeled_id}/labeled", json={"labeled": True}).status_code == 200
    unlabeled_id = _create_test_session(client, "proj")

    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    assert _test_session_ids(client, "proj") == {labeled_id}


def test_revert_does_not_delete_labeled_test_sessions(client):
    _upload_activate_and_establish_state(client, "proj")
    client.put("/api/projects/proj/files/behaviour/notes.txt", content=b"edited after publish")
    session_id = _create_test_session(client, "proj")
    assert client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True}).status_code == 200

    assert client.post("/api/projects/proj/revert").status_code == 200

    assert session_id in _test_session_ids(client, "proj")


def test_a_no_op_publish_still_deletes_test_sessions(client):
    """A double-click publish (nothing actually changed) still clears
    stale Test sessions rather than risking one surviving under a
    coincidentally matching revision number."""
    _upload_activate_and_establish_state(client, "proj")
    session_id = _create_test_session(client, "proj")

    resp = client.post("/api/projects/proj/publish", json={})
    assert resp.status_code == 200, resp.text

    assert session_id not in _test_session_ids(client, "proj")


def test_revert_deletes_every_test_session(client):
    _upload_activate_and_establish_state(client, "proj")
    client.put("/api/projects/proj/files/behaviour/notes.txt", content=b"edited after publish")
    session_id = _create_test_session(client, "proj")
    assert session_id in _test_session_ids(client, "proj")

    assert client.post("/api/projects/proj/revert").status_code == 200

    assert _test_session_ids(client, "proj") == set()


def test_a_no_op_revert_does_not_delete_test_sessions(client):
    """A Test session created against an already-published, unedited
    draft is still valid — nothing about it went stale, so a no-op revert
    must not delete it."""
    _upload_activate_and_establish_state(client, "proj")
    session_id = _create_test_session(client, "proj")

    resp = client.post("/api/projects/proj/revert")
    assert resp.status_code == 200, resp.text

    assert session_id in _test_session_ids(client, "proj")


def test_native_sessions_are_unaffected_by_publish(client):
    _upload_activate_and_establish_state(client, "proj")
    native_session_id = client.get("/api/chat/session").json()["id"]

    client.put("/api/projects/proj/files/behaviour/notes.txt", content=b"edited")
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    sessions = client.get("/api/projects/proj/sessions").json()
    assert any(s["id"] == native_session_id for s in sessions)


def test_imported_sessions_are_unaffected_by_publish(client):
    _upload_activate_and_establish_state(client, "proj")
    response = client.post(
        "/api/projects/proj/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    assert response.status_code == 200, response.text
    imported_session_id = parse_sse_result(response)["last_session_id"]

    client.put("/api/projects/proj/files/behaviour/notes.txt", content=b"edited")
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    sessions = client.get("/api/projects/proj/sessions", params={"include_imported": True}).json()
    assert any(s["id"] == imported_session_id for s in sessions)
