"""POST /api/skills/platform/projects/{project_id}/publish — freezes the current draft
as the new published revision (see ProjectService.publish_project/
Db.publish_project). Backs the "Publish" button (see EditProjectView.vue).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result
from db.models import EditHistory, User

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
        "/api/skills/platform/projects/upload",
        content=_zip_of({"index.yml": f"project:\n  id: {project_id}\n" + MINIMAL_YML, "notes.txt": "original"}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == project_id
    assert client.post(f"/api/skills/platform/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200


def test_publish_clears_undo_history(client):
    _upload_activate_publish(client, "proj")
    client.put(f"/api/skills/platform/projects/proj/files/notes.txt", content=b"edited")
    assert client.get("/api/skills/platform/projects/proj/files/notes.txt").json()["can_undo"] is True

    resp = client.post("/api/skills/platform/projects/proj/publish", json={})
    assert resp.status_code == 200, resp.text

    assert client.get("/api/skills/platform/projects/proj/files/notes.txt").json()["can_undo"] is False


def test_publish_is_a_no_op_when_already_up_to_date(client):
    _upload_activate_publish(client, "proj")
    before = client.get("/api/skills/platform/projects/proj/revision").json()

    resp = client.post("/api/skills/platform/projects/proj/publish", json={})
    assert resp.status_code == 200
    assert {key: resp.json()[key] for key in before} == before


@pytest.mark.regression
def test_publish_clears_every_users_undo_trail_even_when_the_revision_is_already_published(client):
    _upload_activate_publish(client, "proj")
    client.put("/api/skills/platform/projects/proj/files/notes.txt", content=b"edited")
    assert client.get("/api/skills/platform/projects/proj/files/notes.txt").json()["can_undo"] is True
    other = User.create(id="other@example.com", email="other@example.com", role="admin")
    EditHistory.create(
        user_id=other.id, project_id="proj", archive_name="notes.txt", kind="undo", seq=0, content=b"theirs",
    )

    assert client.post("/api/skills/platform/projects/proj/publish", json={}).status_code == 200
    assert not EditHistory.select().where(EditHistory.project_id == "proj").exists()

    EditHistory.create(
        user_id=other.id, project_id="proj", archive_name="notes.txt", kind="undo", seq=0, content=b"dangling",
    )
    assert client.post("/api/skills/platform/projects/proj/publish", json={}).status_code == 200
    assert not EditHistory.select().where(EditHistory.project_id == "proj").exists()
