from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from conftest import parse_sse_result
from session import Session

pytestmark = pytest.mark.regression

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"
MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _build_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload(client, sample):
    """Returns the project's own id — declared entirely by the upload's
    own content (project.id), never a name chosen via the URL (there is
    no URL name anymore, see POST /api/projects/upload)."""
    content = (SAMPLES_DIR / sample).read_bytes()
    response = client.post("/api/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    return parse_sse_result(response)["project_id"]


def _upload_yaml(client, project_id: str):
    yml = f"project:\n  id: {project_id}\n" + MINIMAL_YML
    response = client.post(
        "/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    return response, yml


def test_a_bare_yaml_upload_creates_activates_and_reports_the_project_it_declares(client):
    """The response body must reflect the created project, not be null,
    and both the first upload and a later, unrelated one must activate
    whatever was just uploaded."""
    response, yml = _upload_yaml(client, "proj")
    assert parse_sse_result(response) == {"success": True, "project_id": "proj"}
    assert client.get("/api/projects/proj/files/index.yml").json()["content"] == yml
    assert client.get("/api/projects").json()["active"] == "proj"

    _upload_yaml(client, "second")
    assert client.get("/api/projects").json()["active"] == "second"


def test_a_fresh_install_has_no_active_project_and_still_reports_the_configured_token_budgets(client):
    """A user with no Settings row yet has genuinely no active project,
    not a "default" that may or may not actually exist."""
    projects = client.get("/api/projects").json()
    assert projects["projects"] == []
    assert projects["active"] is None

    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()
    assert state.json()["input_token_budget_per_turn"] == 16000
    assert state.json()["total_token_budget_per_session"] == 200000


def test_deleting_the_active_project_falls_back_to_a_remaining_one_and_degrades_gracefully_when_none_is_left(client):
    hello = _upload(client, "Hello world.zip")
    cat = _upload(client, "Aprendr català.zip")
    client.post(f"/api/projects/{cat}/publish", json={})
    client.put(f"/api/projects/{hello}/activate")

    assert client.delete(f"/api/projects/{hello}").status_code == 200

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"id": cat, "is_paused": False, "ui_label": None}]
    assert projects["active"] == cat
    # The fallback must actually be activated, not just recorded by id.
    assert client.get("/api/chat/session").status_code == 200

    assert client.delete(f"/api/projects/{cat}").status_code == 200
    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()
    assert client.get("/api/projects").json()["active"] is None


def test_default_is_just_an_id_like_any_other_and_can_be_deleted(client):
    _upload_yaml(client, "default")

    assert client.delete("/api/projects/default").status_code == 200
    assert client.get("/api/projects").json()["projects"] == []


def test_an_image_aspect_asset_keeps_its_content_type_across_an_export_reimport_round_trip(client):
    """GET /api/projects/{id} (download) is documented to round-trip
    back through the upload endpoint with no transformation — an image
    asset's own content_type must survive that too, not just its bytes
    (regression: content_type used to resolve every re-imported file
    through the text-only extension map, silently mislabeling every
    image asset, SVG included, on re-upload). Re-uploading the exact
    same content lands as a new revision of the same project (see
    ProjectManager.put_project), not a separate one."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
    yml = "project:\n  id: proj\n" + MINIMAL_YML
    zip_bytes = _build_zip({"index.yml": yml.encode(), "aspect/icon.svg": svg})

    first = client.post("/api/projects/upload", content=zip_bytes, headers={"Content-Type": "application/zip"})
    assert first.status_code == 200, first.text
    project_id = parse_sse_result(first)["project_id"]
    assert client.get(f"/api/projects/{project_id}/files/aspect/icon.svg").json()["content_type"] == "image/svg+xml"

    downloaded = client.get(f"/api/projects/{project_id}")
    assert downloaded.status_code == 200, downloaded.text

    reimport = client.post("/api/projects/upload", content=downloaded.content, headers={"Content-Type": "application/zip"})
    assert reimport.status_code == 200, reimport.text
    assert parse_sse_result(reimport)["project_id"] == project_id
    assert client.get(f"/api/projects/{project_id}/files/aspect/icon.svg").json()["content_type"] == "image/svg+xml"


def test_new_project_creates_activates_and_de_duplicates_the_hello_world_template(client):
    """POST /api/projects creates and activates a "Hello world" project
    without requiring an id up front — the template's own declared id
    ("hello_world") is used as-is on the first call (see
    ProjectManager.create_new_project)."""
    response = client.post("/api/projects")
    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == "hello_world"
    assert client.get("/api/projects").json()["active"] == "hello_world"

    # The template's own content really is what got persisted.
    assert "hello, world" in client.get("/api/projects/hello_world/files/index.yml").json()["content"].lower()
    # It's actually usable, not just a stored blob — already published by
    # the upload itself, but re-publishing must stay a harmless no-op.
    assert client.post("/api/projects/hello_world/publish", json={}).status_code == 200
    assert client.get("/api/chat/session").status_code == 200

    assert client.post("/api/projects").json()["project_id"] == "hello_world_2"
    assert client.post("/api/projects").json()["project_id"] == "hello_world_3"
    assert client.get("/api/projects").json()["projects"] == [
        {"id": "hello_world", "is_paused": False, "ui_label": "Hello, world!"},
        {"id": "hello_world_2", "is_paused": False, "ui_label": "Hello, world!"},
        {"id": "hello_world_3", "is_paused": False, "ui_label": "Hello, world!"},
    ]


def test_a_plain_user_only_sees_the_projects_they_have_a_userproject_row_for(app_db, client):
    """See Db.list_projects_with_availability_for_user — every other role
    (the default test session's own "supervisor", and admin) still sees
    everything, per the tests above."""
    hello = _upload(client, "Hello world.zip")
    _upload(client, "Aprendr català.zip")
    Session().role = "user"

    assert client.get("/api/projects").json()["projects"] == []

    Session().role = "supervisor"
    app_db.record_terms_acceptance(Session().user, hello, archive_id=None)
    Session().role = "user"

    assert [p["id"] for p in client.get("/api/projects").json()["projects"]] == [hello]
