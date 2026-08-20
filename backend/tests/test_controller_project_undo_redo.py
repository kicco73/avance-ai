"""Integration tests (through the real HTTP surface, TestClient) for the
Archive/History redesign: GET .../files/{file_name} exposes {content,
can_undo, can_redo} for a file's current content, POST .../undo and
.../redo walk the current user's own per-file undo/redo trail, and
DELETE .../history clears it — see backend/src/db.py's Archive/History
models and project/project_service.py's put_project_file/undo_project_file/
redo_project_file/clear_project_history.

POST .../undo and .../redo are a pure editor preview, not a save: the
request body is whatever the editor currently shows (mirroring
EditProjectView.vue's own applyHistoryNavigation), and neither endpoint
ever touches Archive — only PUT .../files/{file_name} (a real Save) does.
"""
from __future__ import annotations

import io
import zipfile

import pytest


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _upload(client, project_name: str, files: dict[str, str] | None = None):
    files = files or {"index.yml": MINIMAL_YML, "notes.txt": "hello attachment"}
    response = client.put(
        f"/api/projects/{project_name}", content=_zip_of(files), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    return files


@pytest.mark.contract
def test_uploading_a_project_saves_index_yml_with_no_undo_or_redo_yet(client):
    _upload(client, "proj")

    response = client.get("/api/projects/proj/files/index.yml")

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == MINIMAL_YML
    assert body["can_undo"] is False
    assert body["can_redo"] is False


@pytest.mark.regression
def test_editing_a_file_enables_undo(client):
    _upload(client, "proj")

    new_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi again\n"
    response = client.put("/api/projects/proj/files/index.yml", content=new_yml.encode())

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == new_yml
    assert body["can_undo"] is True
    assert body["can_redo"] is False


@pytest.mark.regression
def test_editing_one_file_does_not_touch_a_siblings_undo_state(client):
    """No more project-wide version counter: editing index.yml alone must
    not enable undo for notes.txt, which was never itself re-saved."""
    _upload(client, "proj")

    new_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi again\n"
    client.put("/api/projects/proj/files/index.yml", content=new_yml.encode())

    notes = client.get("/api/projects/proj/files/notes.txt").json()
    assert notes["content"] == "hello attachment"
    assert notes["can_undo"] is False


@pytest.mark.regression
def test_saving_a_file_with_unchanged_content_is_a_no_op(client):
    _upload(client, "proj")

    response = client.put("/api/projects/proj/files/notes.txt", content=b"hello attachment")

    assert response.status_code == 200
    assert response.json()["can_undo"] is False


@pytest.mark.regression
def test_undo_previews_the_previous_content_without_saving_it(client):
    _upload(client, "proj")
    v1_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: v1\n"
    client.put("/api/projects/proj/files/index.yml", content=v1_yml.encode())

    response = client.post("/api/projects/proj/files/index.yml/undo", content=v1_yml.encode())

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == MINIMAL_YML
    assert body["can_undo"] is False
    assert body["can_redo"] is True
    # Undo never touches Archive — GET still reflects the last real save.
    assert client.get("/api/projects/proj/files/index.yml").json()["content"] == v1_yml


@pytest.mark.contract
def test_undo_with_nothing_to_undo_is_a_400(client):
    _upload(client, "proj")

    response = client.post("/api/projects/proj/files/index.yml/undo", content=MINIMAL_YML.encode())

    assert response.status_code == 400


@pytest.mark.regression
def test_redo_previews_the_undone_content_without_saving_it(client):
    _upload(client, "proj")
    v1_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: v1\n"
    client.put("/api/projects/proj/files/index.yml", content=v1_yml.encode())
    client.post("/api/projects/proj/files/index.yml/undo", content=v1_yml.encode())

    response = client.post("/api/projects/proj/files/index.yml/redo", content=MINIMAL_YML.encode())

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == v1_yml
    assert body["can_undo"] is True
    assert body["can_redo"] is False
    # Redo never touches Archive either — GET still reflects the last real save.
    assert client.get("/api/projects/proj/files/index.yml").json()["content"] == v1_yml


@pytest.mark.contract
def test_redo_with_nothing_to_redo_is_a_400(client):
    _upload(client, "proj")

    response = client.post("/api/projects/proj/files/index.yml/redo", content=MINIMAL_YML.encode())

    assert response.status_code == 400


@pytest.mark.regression
def test_a_fresh_edit_after_undo_clears_redo(client):
    _upload(client, "proj")
    v1_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: v1\n"
    client.put("/api/projects/proj/files/index.yml", content=v1_yml.encode())
    client.post("/api/projects/proj/files/index.yml/undo", content=v1_yml.encode())

    v2_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: v2\n"
    response = client.put("/api/projects/proj/files/index.yml", content=v2_yml.encode())

    assert response.json()["can_redo"] is False
    assert client.post("/api/projects/proj/files/index.yml/redo", content=MINIMAL_YML.encode()).status_code == 400


@pytest.mark.contract
def test_undo_for_an_unknown_project_is_404(client):
    response = client.post("/api/projects/does-not-exist/files/index.yml/undo")
    assert response.status_code == 404


@pytest.mark.regression
def test_clear_history_disables_undo_and_redo_but_keeps_current_content(client):
    _upload(client, "proj")
    v1_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: v1\n"
    client.put("/api/projects/proj/files/index.yml", content=v1_yml.encode())

    response = client.delete("/api/projects/proj/history")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    body = client.get("/api/projects/proj/files/index.yml").json()
    assert body["content"] == v1_yml
    assert body["can_undo"] is False
    assert client.post("/api/projects/proj/files/index.yml/undo", content=v1_yml.encode()).status_code == 400


@pytest.mark.contract
def test_clear_history_for_an_unknown_project_is_404(client):
    response = client.delete("/api/projects/does-not-exist/history")
    assert response.status_code == 404


@pytest.mark.regression
def test_deleting_a_project_file_removes_its_undo_history_too(client):
    _upload(client, "proj")
    client.put("/api/projects/proj/files/notes.txt", content=b"v1")

    response = client.delete("/api/projects/proj/files/notes.txt")

    assert response.status_code == 200
    assert client.get("/api/projects/proj/files/notes.txt").status_code == 404


@pytest.mark.regression
def test_reuploading_an_identical_zip_is_a_no_op(client):
    files = _upload(client, "proj")

    client.put("/api/projects/proj", content=_zip_of(files), headers={"Content-Type": "application/zip"})

    assert client.get("/api/projects/proj/files/index.yml").json()["can_undo"] is False


TWO_STATE_YML = (
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        ui-label: Go\n"
    "        ui-button: Go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


@pytest.mark.regression
def test_undo_does_not_reset_or_reload_the_active_conversation(client):
    """Regression test: undo (and, by the same code path, redo) must
    never trigger the active-conversation reconciliation a real Save
    does (see ProjectService._finalize_project_update) — previously
    undo persisted straight to Archive and called it too, which could
    reset the live conversation as a side effect of what's meant to be
    a purely-preview action."""
    resp = client.put(
        "/api/projects/proj2", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"}
    )
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/projects/proj2/publish", json={})
    assert resp.status_code == 200, resp.text
    session = client.get("/api/chat/session").json()
    action_resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go"})
    assert action_resp.status_code == 200, action_resp.text
    assert action_resp.json()["state"]["key"] == "b"

    # A real edit that leaves "b" untouched (adds unrelated state "c"),
    # so the conversation survives this Save and undo has something to
    # preview.
    yml_v2 = TWO_STATE_YML + "  c:\n    contextual-prompt: extra\n"
    resp = client.put("/api/projects/proj2/files/index.yml", content=yml_v2.encode())
    assert resp.status_code == 200, resp.text
    assert client.get("/api/state").json()["key"] == "b"

    undo_resp = client.post("/api/projects/proj2/files/index.yml/undo", content=yml_v2.encode())
    assert undo_resp.status_code == 200, undo_resp.text

    # The conversation is completely untouched by the undo preview.
    sessions = client.get("/api/projects/proj2/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]
    assert client.get("/api/state").json()["key"] == "b"
