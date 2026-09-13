"""The operator's own database routes (server_admin_controller.py):
backup, restore, and the two clean-ups under /settings/database.
"""
from __future__ import annotations

import sqlite3

import pytest

from conftest import enter_chat, session_of


def _make_sqlite_bytes(tmp_path, name, ddl_statements):
    path = tmp_path / name
    conn = sqlite3.connect(path)
    for statement in ddl_statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path.read_bytes()


@pytest.mark.contract
def test_download_backup_returns_a_sqlite_file(client):
    response = client.get("/api/skills/platform/settings/backup")

    assert response.status_code == 200
    assert response.content.startswith(b"SQLite format 3\x00")
    assert response.headers["content-disposition"].endswith('.sqlite"')


@pytest.mark.contract
def test_restore_a_valid_backup_succeeds(client):
    backup = client.get("/api/skills/platform/settings/backup").content

    response = client.post(
        "/api/skills/platform/settings/backup", content=backup, headers={"Content-Type": "application/octet-stream"}
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.contract
def test_restore_rejects_a_schema_mismatch(client, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated (id INTEGER PRIMARY KEY)"])

    response = client.post(
        "/api/skills/platform/settings/backup", content=wrong, headers={"Content-Type": "application/octet-stream"}
    )

    assert response.status_code == 400
    assert "schema" in response.json()["error"]["message"].lower()


@pytest.mark.regression
def test_app_keeps_working_after_a_rejected_restore(client, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated (id INTEGER PRIMARY KEY)"])
    client.post("/api/skills/platform/settings/backup", content=wrong, headers={"Content-Type": "application/octet-stream"})

    assert client.get("/api/core/state").status_code == 200


@pytest.mark.regression
def test_switching_projects_right_after_a_restore_does_not_crash(client, hello_project):
    """Regression: restore_backup() reconnects peewee's thread-local
    connection on the event-loop thread, so db-touching endpoints must
    stay `async def` to share that thread rather than a threadpool one."""
    backup = client.get("/api/skills/platform/settings/backup").content

    response = client.post(
        "/api/skills/platform/settings/backup", content=backup, headers={"Content-Type": "application/octet-stream"}
    )
    assert response.status_code == 200

    response = client.post(f"/api/core/projects/{hello_project}/activate")
    assert response.status_code == 200

    assert session_of(enter_chat(client, hello_project))


@pytest.mark.contract
def test_wipe_all_live_sessions_deletes_sessions_across_every_project(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))
    assert client.get(f"/api/core/sessions/{session_id}/history").status_code == 200

    response = client.post("/api/skills/platform/settings/database/wipe-live-sessions")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get(f"/api/core/sessions/{session_id}/history").status_code == 404

    assert client.get(f"/api/skills/platform/projects/{hello_project}").status_code == 200


@pytest.mark.contract
def test_clean_unused_revisions_deletes_only_superseded_unpublished_drafts(client, hello_project):
    """hello_project leaves revision 0 published; editing a file forks a
    draft, and publishing it makes revision 0 the superseded one. The
    second edit forks revision 2, the draft that must survive."""
    assert client.put(f"/api/skills/platform/projects/{hello_project}/files/index.css", content=b"/* v1 */").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{hello_project}/publish", json={}).status_code == 200
    assert client.put(f"/api/skills/platform/projects/{hello_project}/files/index.css", content=b"/* v2 */").status_code == 200

    response = client.post("/api/skills/platform/settings/database/clean-unused-revisions")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["deleted"] == 1

    assert client.get(f"/api/skills/platform/projects/{hello_project}/files/index.css").json()["content"] == "/* v2 */"
