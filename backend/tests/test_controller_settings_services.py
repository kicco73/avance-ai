from __future__ import annotations

import io
import zipfile

import pytest

MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.contract
def test_get_services_returns_the_configured_snapshot_verbatim(client):
    response = client.get("/api/settings/services")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"chat", "testing", "ai", "talk", "listen", "database"}
    assert body["database"]["url"] == "sqlite:///test.db"
    assert body["ai"]["providers"][0]["driver"] == "fake"


@pytest.mark.contract
def test_wipe_all_live_sessions_deletes_sessions_across_every_project(client, hello_project):
    session_id = client.get("/api/chat/session").json()["id"]
    assert client.get(f"/api/chat/sessions/{session_id}/messages").status_code == 200

    response = client.post("/api/settings/database/wipe-live-sessions")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get(f"/api/chat/sessions/{session_id}/messages").status_code == 404

    # The project definition itself is untouched — only its live sessions.
    assert client.get(f"/api/projects/{hello_project}").status_code == 200


@pytest.mark.contract
def test_clean_unused_revisions_deletes_only_superseded_unpublished_drafts(client):
    project_id = "clean_me"
    response = client.post(
        "/api/projects/upload",
        content=_zip_of({"index.yml": f"project:\n  id: {project_id}\n" + MINIMAL_YML, "notes.txt": "v0"}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert client.put(f"/api/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/projects/{project_id}/publish", json={}).status_code == 200  # revision 0 published

    assert client.put(f"/api/projects/{project_id}/files/notes.txt", content=b"v1").status_code == 200  # forks to revision 1
    assert client.post(f"/api/projects/{project_id}/publish", json={}).status_code == 200  # revision 1 published — revision 0 now unused

    assert client.put(f"/api/projects/{project_id}/files/notes.txt", content=b"v2").status_code == 200  # forks to revision 2 (draft)

    response = client.post("/api/settings/database/clean-unused-revisions")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    # One revision superseded (revision 0), even though it spans two files
    # (index.yml + notes.txt) — "deleted" counts revisions, not rows.
    assert body["deleted"] == 1

    # The current draft and the still-published revision are untouched.
    assert client.get(f"/api/projects/{project_id}/files/notes.txt").json()["content"] == "v2"
