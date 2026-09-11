"""Integration tests for GET/POST/DELETE .../files/{file_name}, .../undo,
.../redo, and .../history. POST .../undo and .../redo are a pure editor
preview, not a save, and never touch Archive — only PUT .../files/{file_name} does.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _yml(prompt: str = "hi") -> str:
    return f"project:\n  id: proj\ninit-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: {prompt}\n"


MINIMAL_YML = _yml()
V1_YML = _yml("v1")
V2_YML = _yml("v2")


def _upload(client, project_id: str = "proj", files: dict[str, str] | None = None):
    files = files or {"index.yml": MINIMAL_YML, "notes.txt": "hello attachment"}
    response = client.post(
        "/api/projects/upload", content=_zip_of(files), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == project_id
    return files


def _index(client) -> dict:
    return client.get("/api/projects/proj/files/index.yml").json()


def _save(client, content: str):
    return client.put("/api/projects/proj/files/index.yml", content=content.encode())


def _undo(client, showing: str):
    return client.post("/api/projects/proj/files/index.yml/undo", content=showing.encode())


def _redo(client, showing: str):
    return client.post("/api/projects/proj/files/index.yml/redo", content=showing.encode())


@pytest.mark.regression
def test_an_upload_starts_with_no_history_and_only_a_real_content_change_enables_undo_per_file(client):
    """Editing index.yml alone must not enable undo for notes.txt, which
    was never itself re-saved; re-saving identical content is a no-op."""
    _upload(client)

    body = _index(client)
    assert body["content"] == MINIMAL_YML
    assert body["can_undo"] is False
    assert body["can_redo"] is False

    response = _save(client, V1_YML)
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == V1_YML
    assert body["can_undo"] is True
    assert body["can_redo"] is False

    notes = client.get("/api/projects/proj/files/notes.txt").json()
    assert notes["content"] == "hello attachment"
    assert notes["can_undo"] is False
    assert client.put("/api/projects/proj/files/notes.txt", content=b"hello attachment").json()["can_undo"] is False


@pytest.mark.regression
def test_undo_and_redo_preview_without_saving_and_a_fresh_edit_clears_redo(client):
    _upload(client)
    assert _undo(client, MINIMAL_YML).status_code == 400
    assert _redo(client, MINIMAL_YML).status_code == 400

    _save(client, V1_YML)

    response = _undo(client, V1_YML)
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == MINIMAL_YML
    assert body["can_undo"] is False
    assert body["can_redo"] is True
    # Undo never touches Archive — GET still reflects the last real save.
    assert _index(client)["content"] == V1_YML

    response = _redo(client, MINIMAL_YML)
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == V1_YML
    assert body["can_undo"] is True
    assert body["can_redo"] is False
    assert _index(client)["content"] == V1_YML

    _undo(client, V1_YML)
    assert _save(client, V2_YML).json()["can_redo"] is False
    assert _redo(client, MINIMAL_YML).status_code == 400


@pytest.mark.regression
def test_clearing_history_or_deleting_a_file_drops_its_undo_trail_keeping_current_content(client):
    _upload(client)
    _save(client, V1_YML)
    client.put("/api/projects/proj/files/notes.txt", content=b"v1")

    response = client.delete("/api/projects/proj/history")
    assert response.status_code == 200
    assert response.json() == {"success": True}
    body = _index(client)
    assert body["content"] == V1_YML
    assert body["can_undo"] is False
    assert _undo(client, V1_YML).status_code == 400

    assert client.delete("/api/projects/proj/files/notes.txt").status_code == 200
    assert client.get("/api/projects/proj/files/notes.txt").status_code == 404


@pytest.mark.contract
def test_undo_and_clear_history_are_404_for_an_unknown_project(client):
    assert client.post("/api/projects/does-not-exist/files/index.yml/undo").status_code == 404
    assert client.delete("/api/projects/does-not-exist/history").status_code == 404


@pytest.mark.regression
def test_reuploading_an_identical_zip_is_a_no_op(client):
    files = _upload(client)

    response = client.post(
        "/api/projects/upload", content=_zip_of(files), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == "proj"

    assert _index(client)["can_undo"] is False


TWO_STATE_YML = (
    "project:\n  id: proj2\n"
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
    """Undo (and, by the same code path, redo) must never trigger the
    active-conversation reconciliation a real Save does."""
    resp = client.post(
        "/api/projects/upload", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"}
    )
    assert resp.status_code == 200, resp.text
    assert parse_sse_result(resp)["project_id"] == "proj2"
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
