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


def test_put_project_returns_a_success_payload(client):
    """The response body must reflect the created project, not be null."""
    yml = "project:\n  id: proj\n" + MINIMAL_YML
    response = client.post(
        "/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response) == {"success": True, "project_id": "proj"}


def test_fresh_install_has_no_active_project(client):
    """A user with no Settings row yet has genuinely no active project,
    not a "default" that may or may not actually exist."""
    projects = client.get("/api/projects").json()
    assert projects["projects"] == []
    assert projects["active"] is None

    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()


def test_state_reports_the_configured_input_token_budget_per_turn(client):
    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["input_token_budget_per_turn"] == 16000


def test_state_reports_the_configured_total_token_budget_per_session(client):
    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["total_token_budget_per_session"] == 200000


def test_deleting_the_active_project_falls_back_to_a_remaining_one(client):
    """Whatever's left after deleting the active project becomes active."""
    hello = _upload(client, "Hello world.zip")
    cat = _upload(client, "Aprendr català.zip")
    client.post(f"/api/projects/{cat}/publish", json={})
    client.put(f"/api/projects/{hello}/activate")

    response = client.delete(f"/api/projects/{hello}")
    assert response.status_code == 200

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"id": cat, "is_paused": False, "ui_label": None}]
    assert projects["active"] == cat

    # The fallback must actually be activated, not just recorded by id.
    assert client.get("/api/chat/session").status_code == 200


def test_deleting_the_last_project_does_not_crash(client):
    hello = _upload(client, "Hello world.zip")
    client.put(f"/api/projects/{hello}/activate")

    response = client.delete(f"/api/projects/{hello}")
    assert response.status_code == 200

    # GET /api/state must degrade gracefully with no active project left.
    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()

    projects = client.get("/api/projects").json()
    assert projects["active"] is None


def test_default_project_can_be_deleted(client):
    """"default" is just an id, like any other — not specially protected
    from deletion."""
    yml = "project:\n  id: default\n" + MINIMAL_YML
    response = client.post(
        "/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text

    response = client.delete("/api/projects/default")

    assert response.status_code == 200
    assert client.get("/api/projects").json()["projects"] == []


def test_uploading_a_bare_yaml_file_creates_a_single_file_project(client):
    """A bare .yml upload (not a zip) must still be accepted."""
    yml = "project:\n  id: bare\n" + MINIMAL_YML
    response = client.post(
        "/api/projects/upload",
        content=yml.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )

    assert response.status_code == 200, response.text
    body = client.get("/api/projects/bare/files/index.yml").json()
    assert body["content"] == yml


def test_an_image_aspect_asset_keeps_its_content_type_across_an_export_reimport_round_trip(client):
    """GET /api/projects/{id} (download) is documented to round-trip
    back through the upload endpoint with no transformation — an image
    asset's own content_type must survive that too, not just its bytes
    (regression: content_type used to resolve every re-imported file
    through the text-only extension map, silently mislabeling every
    image asset, SVG included, on re-upload). Re-uploading the exact
    same content lands as a new revision of the same project (see
    ProjectManager.put_project), not a separate one — that's exactly
    what's exercised here."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
    yml = "project:\n  id: proj\n" + MINIMAL_YML
    zip_bytes = _build_zip({"index.yml": yml.encode(), "aspect/icon.svg": svg})

    first = client.post("/api/projects/upload", content=zip_bytes, headers={"Content-Type": "application/zip"})
    assert first.status_code == 200, first.text
    project_id = parse_sse_result(first)["project_id"]
    before = client.get(f"/api/projects/{project_id}/files/aspect/icon.svg").json()
    assert before["content_type"] == "image/svg+xml"

    downloaded = client.get(f"/api/projects/{project_id}")
    assert downloaded.status_code == 200, downloaded.text

    reimport = client.post("/api/projects/upload", content=downloaded.content, headers={"Content-Type": "application/zip"})
    assert reimport.status_code == 200, reimport.text
    assert parse_sse_result(reimport)["project_id"] == project_id
    after = client.get(f"/api/projects/{project_id}/files/aspect/icon.svg").json()
    assert after["content_type"] == "image/svg+xml"


def test_uploading_a_project_activates_it_automatically(client):
    """Both the first upload and a later, unrelated one must activate
    the project that was just uploaded."""
    response = client.post(
        "/api/projects/upload",
        content=("project:\n  id: first\n" + MINIMAL_YML).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/projects").json()["active"] == "first"

    response = client.post(
        "/api/projects/upload",
        content=("project:\n  id: second\n" + MINIMAL_YML).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/projects").json()["active"] == "second"


def test_new_project_creates_and_activates_hello_world(client):
    """POST /api/projects creates and activates a "Hello world" project
    without requiring an id up front — the template's own declared id
    ("hello_world") is used as-is on the first call (see
    ProjectManager.create_new_project)."""
    response = client.post("/api/projects")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == "hello_world"

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"id": "hello_world", "is_paused": False, "ui_label": "Hello, world!"}]
    assert projects["active"] == "hello_world"

    # The template's own content really is what got persisted.
    files = client.get("/api/projects/hello_world/files/index.yml").json()
    assert 'hello, world' in files["content"].lower()

    # It's actually usable, not just a stored blob — already published by
    # the upload itself, but re-publishing must stay a harmless no-op.
    publish_resp = client.post("/api/projects/hello_world/publish", json={})
    assert publish_resp.status_code == 200, publish_resp.text
    assert client.get("/api/chat/session").status_code == 200


class TestGetProjectsAsUser:
    """A plain 'user' only sees projects they have a UserProject row for
    (see Db.list_projects_with_availability_for_user) — every other role
    (the default test session's own "supervisor", and admin) still sees
    everything, per the tests above."""

    def test_sees_none_of_the_existing_projects_by_default(self, client):
        _upload(client, "Hello world.zip")
        _upload(client, "Aprendr català.zip")
        Session().role = "user"

        assert client.get("/api/projects").json()["projects"] == []

    def test_sees_only_a_project_they_have_access_to(self, app_db, client):
        hello = _upload(client, "Hello world.zip")
        _upload(client, "Aprendr català.zip")
        app_db.record_terms_acceptance(Session().user, hello, archive_id=None)
        Session().role = "user"

        projects = client.get("/api/projects").json()["projects"]

        assert [p["id"] for p in projects] == [hello]


def test_new_project_de_duplicates_the_id_on_repeat_calls(client):
    first = client.post("/api/projects").json()
    second = client.post("/api/projects").json()
    third = client.post("/api/projects").json()

    assert first["project_id"] == "hello_world"
    assert second["project_id"] == "hello_world_2"
    assert third["project_id"] == "hello_world_3"
    assert client.get("/api/projects").json()["projects"] == [
        {"id": "hello_world", "is_paused": False, "ui_label": "Hello, world!"},
        {"id": "hello_world_2", "is_paused": False, "ui_label": "Hello, world!"},
        {"id": "hello_world_3", "is_paused": False, "ui_label": "Hello, world!"},
    ]
