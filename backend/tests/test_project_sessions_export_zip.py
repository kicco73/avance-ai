"""A project's zip download/upload round-trip for its imported sessions:
download embeds a sessions.json (imported-only) alongside the project's
real files; upload detects that file, never persists it as a project
file, and consumes it to re-import every session automatically.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from session import Session

pytestmark = pytest.mark.contract

MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload_zip(client, project_name: str, files: dict[str, bytes]):
    return client.put(
        f"/api/projects/{project_name}", content=_zip_of(files), headers={"Content-Type": "application/zip"}
    )


def _download_zip(client, project_name: str) -> dict[str, bytes]:
    response = client.get(f"/api/projects/{project_name}")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_download_has_no_sessions_json_when_there_are_no_imported_sessions(client, hello_project):
    files = _download_zip(client, hello_project)
    assert "sessions.json" not in files


def test_download_includes_both_live_and_imported_sessions_relabeled_as_imported(client, app_db, hello_project):
    app_db.set_active_project_name(hello_project, "alice")
    with Session().impersonate("alice"):
        native_session = client.get("/api/chat/session").json()
    assert native_session["type"] == "live"
    resp = client.post(
        "/api/projects/hello/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: yo\n", "text/plain"))]
    )
    assert resp.status_code == 200, resp.text

    files = _download_zip(client, hello_project)

    assert "sessions.json" in files
    sessions = json.loads(files["sessions.json"])
    assert len(sessions) == 2
    assert {s["type"] for s in sessions} == {"imported"}
    assert {s["name"] for s in sessions} == {native_session["title"], "t.txt"}
    assert "alice" in {s["username"] for s in sessions}


def test_sessions_json_never_appears_among_the_projects_own_files(client, hello_project):
    client.post("/api/projects/hello/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: yo\n", "text/plain"))])

    file_list = client.get(f"/api/projects/{hello_project}/files").json()["files"]

    assert "sessions.json" not in file_list
    # Explicitly not persisted as a real archive either.
    assert client.get(f"/api/projects/{hello_project}/files/sessions.json").status_code == 404


def test_uploading_a_zip_with_sessions_json_imports_them_automatically(client):
    sessions_payload = [
        {
            "name": "Reference transcript",
            "username": "User 1",
            "start_state": "a", "end_state": "a", "labeled": True, "comment": "worth keeping",
            "messages": [
                {"role": "user", "text": "hi"},
                {"role": "assistant", "text": "hello"},
            ],
        }
    ]
    resp = _upload_zip(client, "proj", {
        "index.yml": MINIMAL_YML.encode(),
        "sessions.json": json.dumps(sessions_payload).encode(),
    })
    assert resp.status_code == 200, resp.text

    Session().user = "User 1"
    sessions = client.get("/api/projects/proj/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert sessions[0]["type"] == "imported"
    assert sessions[0]["title"] == "Reference transcript"
    messages = client.get(f"/api/chat/sessions/{sessions[0]['id']}/messages").json()
    assert [m["content"] for m in messages] == ["hi", "hello"]


def test_download_then_reupload_round_trips_the_imported_session(client):
    resp = client.put(
        "/api/projects/roundtrip", content=MINIMAL_YML.encode(), headers={"Content-Type": "application/x-yaml"}
    )
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/projects/roundtrip/publish", json={})
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/projects/roundtrip/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: yo\n", "text/plain"))]
    )
    assert resp.status_code == 200, resp.text

    zip_bytes = client.get("/api/projects/roundtrip").content

    resp = client.put(
        "/api/projects/roundtrip-copy", content=zip_bytes, headers={"Content-Type": "application/zip"}
    )
    assert resp.status_code == 200, resp.text

    resp = client.put("/api/projects/roundtrip-copy/activate")
    assert resp.status_code == 200, resp.text
    sessions = client.get("/api/projects/roundtrip-copy/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "t.txt"


def test_download_then_reupload_round_trips_a_live_session_from_another_user(client, app_db):
    resp = client.put(
        "/api/projects/roundtrip2", content=MINIMAL_YML.encode(), headers={"Content-Type": "application/x-yaml"}
    )
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/projects/roundtrip2/publish", json={})
    assert resp.status_code == 200, resp.text
    app_db.set_active_project_name("roundtrip2", "alice")
    with Session().impersonate("alice"):
        live_session = client.get("/api/chat/session").json()
        client.post(f"/api/chat/sessions/{live_session['id']}/messages", json={"message": "hi"})

    zip_bytes = client.get("/api/projects/roundtrip2").content

    resp = client.put(
        "/api/projects/roundtrip2-copy", content=zip_bytes, headers={"Content-Type": "application/zip"}
    )
    assert resp.status_code == 200, resp.text

    sessions = app_db.list_chat_sessions(None, "roundtrip2-copy", type=None)
    assert len(sessions) == 1
    assert sessions[0]["type"] == "imported"
    assert sessions[0]["username"] == "alice"


def test_upload_rejects_the_whole_project_when_sessions_json_is_not_valid_json(client):
    resp = _upload_zip(client, "proj", {
        "index.yml": MINIMAL_YML.encode(),
        "sessions.json": b"not valid json {{{",
    })
    assert resp.status_code == 400
    assert "sessions.json" in resp.json()["error"]["message"]
    # Nothing was persisted at all — not even index.yml.
    assert client.get("/api/projects/proj/files/index.yml").status_code == 404


def test_upload_rejects_the_whole_project_when_sessions_json_is_not_a_list(client):
    resp = _upload_zip(client, "proj", {
        "index.yml": MINIMAL_YML.encode(),
        "sessions.json": json.dumps({"not": "a list"}).encode(),
    })
    assert resp.status_code == 400
    assert "sessions.json" in resp.json()["error"]["message"]


def test_a_malformed_individual_session_is_skipped_others_still_import(client):
    sessions_payload = [
        {"messages": [{"role": "user"}]},  # missing required 'text' — malformed
        {"name": "Good one", "username": "User 1", "messages": [{"role": "user", "text": "hi"}]},
    ]
    resp = _upload_zip(client, "proj", {
        "index.yml": MINIMAL_YML.encode(),
        "sessions.json": json.dumps(sessions_payload).encode(),
    })
    assert resp.status_code == 200, resp.text

    Session().user = "User 1"
    sessions = client.get("/api/projects/proj/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "Good one"
