"""POST /api/projects/{project_id}/publish — freezes the current draft
as the new published revision (see ProjectService.publish_project/
Db.publish_project). Backs the "Publish" button (see EditProjectView.vue).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract

MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload_activate_publish(client, project_id: str):
    response = client.post(
        "/api/projects/upload",
        content=_zip_of({"index.yml": f"project:\n  id: {project_id}\n" + MINIMAL_YML, "notes.txt": "original"}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == project_id
    assert client.put(f"/api/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/projects/{project_id}/publish", json={}).status_code == 200


def test_publish_clears_undo_history(client):
    _upload_activate_publish(client, "proj")
    client.put(f"/api/projects/proj/files/notes.txt", content=b"edited")
    assert client.get("/api/projects/proj/files/notes.txt").json()["can_undo"] is True

    resp = client.post("/api/projects/proj/publish", json={})
    assert resp.status_code == 200, resp.text

    assert client.get("/api/projects/proj/files/notes.txt").json()["can_undo"] is False


def test_publish_is_a_no_op_when_already_up_to_date(client):
    _upload_activate_publish(client, "proj")
    before = client.get("/api/projects/proj/revision").json()

    resp = client.post("/api/projects/proj/publish", json={})
    assert resp.status_code == 200
    assert resp.json() == before
