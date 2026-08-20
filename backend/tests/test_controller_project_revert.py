"""POST /api/projects/{project_name}/revert — discards the in-progress
draft revision, reverting to whatever was last published
(ProjectService.revert_to_published).
"""
from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.contract

MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload_activate_publish(client, project_name: str):
    response = client.put(
        f"/api/projects/{project_name}",
        content=_zip_of({"index.yml": MINIMAL_YML, "notes.txt": "original"}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert client.put(f"/api/projects/{project_name}/activate").status_code == 200
    assert client.post(f"/api/projects/{project_name}/publish", json={}).status_code == 200


def test_revert_restores_an_edited_file_to_its_published_content(client):
    _upload_activate_publish(client, "proj")

    edit_resp = client.put(
        f"/api/projects/proj/files/notes.txt", content=b"edited after publish"
    )
    assert edit_resp.status_code == 200, edit_resp.text
    assert client.get("/api/projects/proj/files/notes.txt").json()["content"] == "edited after publish"
    revision_after_edit = client.get("/api/projects/proj/revision").json()
    assert revision_after_edit["revision"] == revision_after_edit["published_revision"] + 1

    revert_resp = client.post("/api/projects/proj/revert")
    assert revert_resp.status_code == 200, revert_resp.text
    payload = revert_resp.json()
    assert payload["revision"] == payload["published_revision"]

    restored = client.get("/api/projects/proj/files/notes.txt")
    assert restored.json()["content"] == "original"


def test_revert_removes_a_file_created_after_publish(client):
    _upload_activate_publish(client, "proj")

    assert client.put(f"/api/projects/proj/files/brand_new.txt", content=b"new").status_code == 200
    assert "brand_new.txt" in client.get("/api/projects/proj/files").json()["files"]

    assert client.post("/api/projects/proj/revert").status_code == 200

    assert "brand_new.txt" not in client.get("/api/projects/proj/files").json()["files"]
    assert client.get("/api/projects/proj/files/brand_new.txt").status_code == 404


def test_revert_clears_undo_history(client):
    _upload_activate_publish(client, "proj")
    client.put(f"/api/projects/proj/files/notes.txt", content=b"edited")
    assert client.get("/api/projects/proj/files/notes.txt").json()["can_undo"] is True

    client.post("/api/projects/proj/revert")

    assert client.get("/api/projects/proj/files/notes.txt").json()["can_undo"] is False


def test_revert_is_a_no_op_when_the_draft_is_already_published(client):
    _upload_activate_publish(client, "proj")
    before = client.get("/api/projects/proj/revision").json()

    resp = client.post("/api/projects/proj/revert")
    assert resp.status_code == 200
    assert resp.json() == before


def test_revert_is_a_no_op_when_never_published(client):
    response = client.put(
        "/api/projects/proj",
        content=_zip_of({"index.yml": MINIMAL_YML}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert client.put("/api/projects/proj/activate").status_code == 200

    resp = client.post("/api/projects/proj/revert")
    assert resp.status_code == 200
    assert resp.json()["published_revision"] is None


def test_revert_rejects_an_unknown_project(client):
    resp = client.post("/api/projects/does-not-exist/revert")
    assert resp.status_code == 404
