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


def _edit_draft(client, project_name: str) -> None:
    client.put(f"/api/projects/{project_name}/files/behaviour/notes.txt", content=b"edited after publish")


def test_publish_deletes_every_unlabeled_test_session_even_when_nothing_actually_changed(client):
    """Labeling freezes a 'test' session as durable benchmark ground
    truth — same contract as a labeled 'live' session. Test.session
    cascades on delete, so wiping a labeled session would silently destroy
    every TestReplayJob result computed against it. A double-click publish
    (nothing changed) still clears the stale unlabeled ones rather than
    risking one surviving under a coincidentally matching revision."""
    _upload_activate_and_establish_state(client, "proj")
    labeled_id = _create_test_session(client, "proj")
    assert client.put(f"/api/chat/sessions/{labeled_id}/labeled", json={"labeled": True}).status_code == 200
    unlabeled_id = _create_test_session(client, "proj")
    assert unlabeled_id in _test_session_ids(client, "proj")

    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    assert _test_session_ids(client, "proj") == {labeled_id}


def test_revert_deletes_every_unlabeled_test_session_but_only_when_there_was_a_draft_to_revert(client):
    """A Test session created against an already-published, unedited draft
    is still valid — nothing about it went stale, so a no-op revert must
    not delete it."""
    _upload_activate_and_establish_state(client, "proj")
    untouched_id = _create_test_session(client, "proj")

    assert client.post("/api/projects/proj/revert").status_code == 200
    assert untouched_id in _test_session_ids(client, "proj")

    _edit_draft(client, "proj")
    labeled_id = _create_test_session(client, "proj")
    assert client.put(f"/api/chat/sessions/{labeled_id}/labeled", json={"labeled": True}).status_code == 200
    unlabeled_id = _create_test_session(client, "proj")
    assert unlabeled_id in _test_session_ids(client, "proj")

    assert client.post("/api/projects/proj/revert").status_code == 200

    assert _test_session_ids(client, "proj") == {labeled_id}


def test_native_and_imported_sessions_are_both_unaffected_by_publish(client):
    _upload_activate_and_establish_state(client, "proj")
    native_session_id = client.get("/api/chat/session").json()["id"]
    response = client.post(
        "/api/projects/proj/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    assert response.status_code == 200, response.text
    imported_session_id = parse_sse_result(response)["last_session_id"]

    _edit_draft(client, "proj")
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    sessions = client.get("/api/projects/proj/sessions", params={"include_imported": True}).json()
    ids = {s["id"] for s in sessions}
    assert native_session_id in ids
    assert imported_session_id in ids
