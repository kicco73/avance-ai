"""A project's own zip download/upload round-trip for its imported
sessions (see ProjectService.export_project_zip/put_project,
SESSIONS_EXPORT_FILENAME) — download embeds a sessions.json (session_
export.py's own shape, imported-only) alongside the project's real files;
upload detects that same file, never persists it as a project file, and
consumes it to re-import every session automatically. The two are meant
to be exact duals of each other.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

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


def test_download_includes_only_imported_sessions_never_native_ones(client, hello_project):
    # A native session (just bootstrapping the chat).
    native_session = client.get("/api/chat/session").json()
    assert native_session["source"] == "native"
    # An imported session.
    resp = client.post(
        "/api/chat/sessions/import", files={"file": ("t.txt", "user: hi\nassistant: yo\n", "text/plain")}
    )
    assert resp.status_code == 200, resp.text

    files = _download_zip(client, hello_project)

    assert "sessions.json" in files
    sessions = json.loads(files["sessions.json"])
    assert len(sessions) == 1
    assert sessions[0]["name"] == "t.txt"


def test_sessions_json_never_appears_among_the_projects_own_files(client, hello_project):
    client.post("/api/chat/sessions/import", files={"file": ("t.txt", "user: hi\nassistant: yo\n", "text/plain")})

    file_list = client.get(f"/api/projects/{hello_project}/files").json()["files"]

    assert "sessions.json" not in file_list
    # Explicitly not persisted as a real archive either.
    assert client.get(f"/api/projects/{hello_project}/files/sessions.json").status_code == 404


def test_uploading_a_zip_with_sessions_json_imports_them_automatically(client):
    sessions_payload = [
        {
            "name": "Reference transcript",
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

    sessions = client.get("/api/chat/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert sessions[0]["source"] == "imported"
    assert sessions[0]["title"] == "Reference transcript"
    messages = client.get(f"/api/chat/messages?session_id={sessions[0]['id']}").json()
    assert [m["content"] for m in messages] == ["hi", "hello"]


def test_download_then_reupload_round_trips_the_imported_session(client):
    resp = client.put(
        "/api/projects/roundtrip", content=MINIMAL_YML.encode(), headers={"Content-Type": "application/x-yaml"}
    )
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/projects/roundtrip/publish", json={})
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/chat/sessions/import", files={"file": ("t.txt", "user: hi\nassistant: yo\n", "text/plain")}
    )
    assert resp.status_code == 200, resp.text

    zip_bytes = client.get("/api/projects/roundtrip").content

    resp = client.put(
        "/api/projects/roundtrip-copy", content=zip_bytes, headers={"Content-Type": "application/zip"}
    )
    assert resp.status_code == 200, resp.text

    resp = client.put("/api/projects/roundtrip-copy/activate")
    assert resp.status_code == 200, resp.text
    sessions = client.get("/api/chat/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "t.txt"


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
        {"name": "Good one", "messages": [{"role": "user", "text": "hi"}]},
    ]
    resp = _upload_zip(client, "proj", {
        "index.yml": MINIMAL_YML.encode(),
        "sessions.json": json.dumps(sessions_payload).encode(),
    })
    assert resp.status_code == 200, resp.text

    sessions = client.get("/api/chat/sessions?include_imported=true").json()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "Good one"
