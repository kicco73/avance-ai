"""Integration tests (through the real HTTP surface, TestClient) for
project file versioning — see backend/src/db.py's Archive model (a
project-wide version counter, not one per file) and
project/project_service.py's _prepare_project_update/_persist_project_update.
"""
from __future__ import annotations

import io
import re
import zipfile


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


def test_uploading_a_project_stamps_index_yml_at_version_0(client):
    _upload(client, "proj")

    response = client.get("/api/projects/proj/files/index.yml")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 0
    assert body["total_versions"] == 1
    assert body["content"].startswith("version: 0\nlast-changed: ")
    assert "init-action:" in body["content"]


def test_uploading_a_project_does_not_stamp_a_plain_attachment(client):
    _upload(client, "proj")

    response = client.get("/api/projects/proj/files/notes.txt")

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "hello attachment"
    assert body["version"] == 0
    assert body["total_versions"] == 1


def test_editing_index_yml_increments_its_own_version_and_restamps_it(client):
    _upload(client, "proj")

    new_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi again\n"
    response = client.put("/api/projects/proj/files/index.yml", content=new_yml.encode())

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["total_versions"] == 2
    assert body["content"].startswith("version: 1\nlast-changed: ")
    assert "hi again" in body["content"]
    # Exactly one of each stamped field, not accumulated across saves.
    assert body["content"].count("version:") == 1
    assert body["content"].count("last-changed:") == 1


def test_editing_index_yml_also_bumps_every_sibling_file_to_the_same_version(client):
    """The core invariant this whole feature exists for: every file in a
    project always shares one version number — editing index.yml alone
    still carries every other file forward to that same new version."""
    _upload(client, "proj")

    new_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi again\n"
    client.put("/api/projects/proj/files/index.yml", content=new_yml.encode())

    notes = client.get("/api/projects/proj/files/notes.txt").json()
    assert notes["version"] == 1
    assert notes["content"] == "hello attachment"  # carried forward unchanged


def test_editing_an_attachment_also_bumps_index_ymls_version_and_restamps_it(client):
    _upload(client, "proj")

    client.put("/api/projects/proj/files/notes.txt", content=b"updated notes")

    index = client.get("/api/projects/proj/files/index.yml").json()
    assert index["version"] == 1
    assert index["content"].startswith("version: 1\nlast-changed: ")

    notes = client.get("/api/projects/proj/files/notes.txt").json()
    assert notes["content"] == "updated notes"
    assert notes["version"] == 1


def test_saving_a_file_with_unchanged_content_is_a_no_op(client):
    """No genuine change anywhere in the request — the project version
    must not advance at all, for any file."""
    _upload(client, "proj")

    response = client.put("/api/projects/proj/files/notes.txt", content=b"hello attachment")

    assert response.status_code == 200
    assert response.json()["version"] == 0
    assert client.get("/api/projects/proj/files/index.yml").json()["version"] == 0


def test_resaving_index_yml_with_the_same_body_is_a_no_op_despite_the_stamp(client):
    """index.yml's own stamp (last-changed especially) always differs
    byte-for-byte on a fresh write — the no-op check must compare its
    *body*, ignoring the stamp, or every re-save would look like a change."""
    _upload(client, "proj")
    unchanged_body = MINIMAL_YML  # identical to what _upload already saved

    response = client.put("/api/projects/proj/files/index.yml", content=unchanged_body.encode())

    assert response.status_code == 200
    assert response.json()["version"] == 0
    assert client.get("/api/projects/proj/files/notes.txt").json()["version"] == 0


def test_reuploading_an_identical_zip_is_a_no_op(client):
    files = _upload(client, "proj")

    client.put("/api/projects/proj", content=_zip_of(files), headers={"Content-Type": "application/zip"})

    assert client.get("/api/projects/proj/files/index.yml").json()["version"] == 0
    assert client.get("/api/projects/proj/files/notes.txt").json()["version"] == 0


def test_reuploading_a_zip_with_changed_attachment_content_bumps_the_whole_project(client):
    files = _upload(client, "proj")
    files = {**files, "notes.txt": "different content this time"}
    client.put("/api/projects/proj", content=_zip_of(files), headers={"Content-Type": "application/zip"})

    notes = client.get("/api/projects/proj/files/notes.txt").json()
    assert notes["version"] == 1
    assert notes["content"] == "different content this time"
    # index.yml wasn't in the diff itself, but still moves to version 1
    # alongside it, and its embedded stamp reflects that.
    index = client.get("/api/projects/proj/files/index.yml").json()
    assert index["version"] == 1
    assert index["content"].startswith("version: 1\n")


def test_get_file_versions_reports_the_total_count(client):
    _upload(client, "proj")
    client.put("/api/projects/proj/files/notes.txt", content=b"v1")
    client.put("/api/projects/proj/files/notes.txt", content=b"v2")

    response = client.get("/api/projects/proj/files/index.yml/versions")

    assert response.status_code == 200
    # index.yml itself was never directly re-edited, but was carried
    # forward (and re-stamped) at each of the two later saves too.
    assert response.json() == {"total_versions": 3}


def test_get_file_versions_is_zero_for_a_file_that_was_never_saved(client):
    response = client.get("/api/projects/proj/files/does-not-exist.txt/versions")

    assert response.status_code == 200
    assert response.json() == {"total_versions": 0}


def test_get_file_at_an_exact_version(client):
    _upload(client, "proj")
    v1_yml = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: v1\n"
    client.put("/api/projects/proj/files/index.yml", content=v1_yml.encode())

    response = client.get("/api/projects/proj/files/index.yml/versions/0")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 0
    assert body["total_versions"] == 2
    assert "version: 0\n" in body["content"]


def test_get_file_at_a_version_beyond_the_latest_is_404(client):
    """No more "clamp to the latest" — a version that doesn't exist is a
    hard miss, even if it's higher than the current one."""
    _upload(client, "proj")

    response = client.get("/api/projects/proj/files/index.yml/versions/999")

    assert response.status_code == 404


def test_get_file_below_every_stored_version_is_404(client):
    _upload(client, "proj")
    client.put("/api/projects/proj/files/notes.txt", content=b"v1")

    response = client.get("/api/projects/proj/files/index.yml/versions/-1")

    assert response.status_code == 404


def test_delete_versions_prunes_older_versions_but_keeps_the_latest(client):
    _upload(client, "proj")
    client.put("/api/projects/proj/files/notes.txt", content=b"v1")
    client.put("/api/projects/proj/files/notes.txt", content=b"v2")

    response = client.delete("/api/projects/proj/versions")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert client.get("/api/projects/proj/files/index.yml/versions").json() == {"total_versions": 1}
    # The current content is still there — pruning history never touches
    # the live/latest content.
    latest = client.get("/api/projects/proj/files/index.yml")
    assert latest.json()["version"] == 2
    assert client.get("/api/projects/proj/files/index.yml/versions/0").status_code == 404


def test_delete_versions_for_an_unknown_project_is_404(client):
    response = client.delete("/api/projects/does-not-exist/versions")
    assert response.status_code == 404


def test_deleting_a_project_file_removes_its_whole_version_history(client):
    _upload(client, "proj")
    client.put("/api/projects/proj/files/notes.txt", content=b"v1")

    response = client.delete("/api/projects/proj/files/notes.txt")

    assert response.status_code == 200
    assert client.get("/api/projects/proj/files/notes.txt/versions").json() == {"total_versions": 0}


def test_last_changed_timestamp_is_iso_formatted(client):
    _upload(client, "proj")

    content = client.get("/api/projects/proj/files/index.yml").json()["content"]
    match = re.search(r"^last-changed: (.+)$", content, re.MULTILINE)

    assert match is not None
    # datetime.fromisoformat round-trips iff it's a valid ISO 8601 string.
    from datetime import datetime

    datetime.fromisoformat(match.group(1))
