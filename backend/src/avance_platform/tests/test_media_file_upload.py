"""The Media node's own storage contract: PDF/image/Markdown/audio files
live under `media/<basename>`, uploaded and read through the same
generic file endpoints every other archive uses (edit_project_controller.py)
— nothing Media-specific exists there. Image/audio/PDF extensions
canonicalize into `media/` on their own now (see automaton.file_types —
an aspect asset moved there too, see test_media_file_upload's own
migration coverage in test_automaton_builder_media.py's sibling,
test_migrate_legacy_media_assets.py). `.md` is the one extension still
shared with `behaviour/` (a behaviour attachment), which is what
ArchiveLayout.canonicalize_name's own two-segment "media/<name>" special
case (layout.py) exists for — the same way "sources/<name>.csv" already
special-cases a second folder for an extension otherwise reserved
elsewhere."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract


def test_a_pdf_uploads_under_media_and_is_readable_back(client, hello_project):
    put = client.put(
        f"/api/skills/platform/projects/{hello_project}/files/media/report.pdf",
        content=b"%PDF-1.4 fake",
        headers={"Content-Type": "application/pdf"},
    )
    assert put.status_code == 200, put.text

    files = client.get(f"/api/skills/platform/projects/{hello_project}/files").json()["files"]
    assert "media/report.pdf" in files

    got = client.get(f"/api/skills/platform/projects/{hello_project}/files/media/report.pdf")
    assert got.status_code == 200
    assert got.json()["content"] is None
    assert got.json()["media_type"] == "application/pdf"

    content = client.get(f"/api/core/projects/{hello_project}/files/media/report.pdf/content")
    assert content.status_code == 200
    assert content.content == b"%PDF-1.4 fake"


def test_a_mismatched_content_type_is_rejected(client, hello_project):
    put = client.put(
        f"/api/skills/platform/projects/{hello_project}/files/media/report.pdf",
        content=b"%PDF-1.4 fake",
        headers={"Content-Type": "image/png"},
    )
    assert put.status_code == 400


def test_a_markdown_media_file_lives_under_media_distinct_from_a_behaviour_attachment_of_the_same_basename(client, hello_project):
    client.put(f"/api/skills/platform/projects/{hello_project}/files/media/notes.md", content="# Media doc\n")
    client.put(f"/api/skills/platform/projects/{hello_project}/files/behaviour/notes.md", content="# Attachment\n")

    files = client.get(f"/api/skills/platform/projects/{hello_project}/files").json()["files"]
    assert "media/notes.md" in files
    assert "behaviour/notes.md" in files

    media_doc = client.get(f"/api/skills/platform/projects/{hello_project}/files/media/notes.md").json()
    attachment = client.get(f"/api/skills/platform/projects/{hello_project}/files/behaviour/notes.md").json()
    assert media_doc["content"] == "# Media doc\n"
    assert attachment["content"] == "# Attachment\n"


def test_renaming_a_media_image_keeps_it_under_media(client, hello_project):
    client.put(
        f"/api/skills/platform/projects/{hello_project}/files/media/photo.png",
        content=b"\x89PNG fake",
        headers={"Content-Type": "image/png"},
    )

    renamed = client.post(
        f"/api/skills/platform/projects/{hello_project}/files/media/photo.png/rename",
        json={"new_name": "portrait.png"},
    )
    assert renamed.status_code == 200, renamed.text

    files = client.get(f"/api/skills/platform/projects/{hello_project}/files").json()["files"]
    assert "media/portrait.png" in files


def test_downloading_then_reuploading_keeps_a_media_file_and_its_content(client, hello_project):
    client.put(
        f"/api/skills/platform/projects/{hello_project}/files/media/report.pdf",
        content=b"%PDF-1.4 fake",
        headers={"Content-Type": "application/pdf"},
    )
    client.post(f"/api/skills/platform/projects/{hello_project}/publish", json={})

    download = client.get(f"/api/skills/platform/projects/{hello_project}")
    assert download.status_code == 200
    bumped_zip = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(download.content)) as src, zipfile.ZipFile(bumped_zip, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "index.yml":
                data = re.sub(rb"revision:\s*\d+", b"revision: 999", data, count=1)
            dst.writestr(item, data)

    response = client.post(
        "/api/skills/platform/projects/upload", content=bumped_zip.getvalue(), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == hello_project

    content = client.get(f"/api/core/projects/{hello_project}/files/media/report.pdf/content")
    assert content.status_code == 200
    assert content.content == b"%PDF-1.4 fake"
