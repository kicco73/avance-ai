from __future__ import annotations

import sqlite3

import pytest


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
    response = client.get("/api/settings/backup")

    assert response.status_code == 200
    assert response.content.startswith(b"SQLite format 3\x00")
    assert response.headers["content-disposition"].endswith('.sqlite"')


@pytest.mark.contract
def test_restore_a_valid_backup_succeeds(client):
    backup = client.get("/api/settings/backup").content

    response = client.post(
        "/api/settings/backup", content=backup, headers={"Content-Type": "application/octet-stream"}
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.contract
def test_restore_rejects_a_schema_mismatch(client, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated (id INTEGER PRIMARY KEY)"])

    response = client.post(
        "/api/settings/backup", content=wrong, headers={"Content-Type": "application/octet-stream"}
    )

    assert response.status_code == 400
    assert "schema" in response.json()["error"]["message"].lower()


@pytest.mark.regression
def test_app_keeps_working_after_a_rejected_restore(client, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated (id INTEGER PRIMARY KEY)"])
    client.post("/api/settings/backup", content=wrong, headers={"Content-Type": "application/octet-stream"})

    assert client.get("/api/state").status_code == 200


@pytest.mark.regression
def test_switching_projects_right_after_a_restore_does_not_crash(client, hello_project):
    """Regression: restore_backup() reconnects peewee's thread-local
    connection on the event-loop thread, so db-touching endpoints must
    stay `async def` to share that thread rather than a threadpool one."""
    backup = client.get("/api/settings/backup").content

    response = client.post(
        "/api/settings/backup", content=backup, headers={"Content-Type": "application/octet-stream"}
    )
    assert response.status_code == 200

    response = client.put(f"/api/projects/{hello_project}/activate")
    assert response.status_code == 200

    # The bootstrap call the frontend makes right after any switch.
    response = client.get("/api/chat/session")
    assert response.status_code == 200
