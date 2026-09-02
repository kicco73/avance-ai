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


def _upload(client, name, sample):
    """Returns the project's actual name — put_project derives it from
    the upload's own project.id/project.ui-label when declared, so it
    need not match `name`, the fallback used only when neither is."""
    content = (SAMPLES_DIR / sample).read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    return parse_sse_result(response)["project_name"]


def test_put_project_returns_a_success_payload(client):
    """The response body must reflect the created project, not be null."""
    response = client.put(
        "/api/projects/proj", content=MINIMAL_YML.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response) == {"success": True, "project_name": "proj"}


def test_put_project_uses_the_declared_ui_label_over_the_url_name(client):
    content = (SAMPLES_DIR / "Hello world.zip").read_bytes()
    response = client.put("/api/projects/proj", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    assert parse_sse_result(response) == {"success": True, "project_name": "Hello, world!"}


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
    hello = _upload(client, "hello", "Hello world.zip")
    _upload(client, "cat", "Aprendr català.zip")
    client.post("/api/projects/cat/publish", json={})
    client.put(f"/api/projects/{hello}/activate")

    response = client.delete(f"/api/projects/{hello}")
    assert response.status_code == 200

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"name": "cat", "is_paused": False, "ui_label": None}]
    assert projects["active"] == "cat"

    # The fallback must actually be activated, not just recorded by name.
    assert client.get("/api/chat/session").status_code == 200


def test_deleting_the_last_project_does_not_crash(client):
    hello = _upload(client, "hello", "Hello world.zip")
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
    """"default" is just a name, like any other — not specially protected
    from deletion."""
    response = client.put(
        "/api/projects/default", content=MINIMAL_YML.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text

    response = client.delete("/api/projects/default")

    assert response.status_code == 200
    assert client.get("/api/projects").json()["projects"] == []


def test_uploading_a_bare_yaml_file_creates_a_single_file_project(client):
    """A bare .yml upload (not a zip) must still be accepted."""
    response = client.put(
        "/api/projects/bare",
        content=MINIMAL_YML.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )

    assert response.status_code == 200, response.text
    body = client.get("/api/projects/bare/files/index.yml").json()
    assert body["content"] == MINIMAL_YML


def test_an_image_aspect_asset_keeps_its_content_type_across_an_export_reimport_round_trip(client):
    """GET /api/projects/{name} (download) is documented to round-trip
    back through PUT with no transformation — an image asset's own
    content_type must survive that too, not just its bytes (regression:
    _persist_new_project used to resolve every re-imported file's
    content_type through the text-only extension map, silently
    mislabeling every image asset, SVG included, on re-upload)."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
    zip_bytes = _build_zip({"index.yml": MINIMAL_YML.encode(), "aspect/icon.svg": svg})

    first = client.put("/api/projects/proj", content=zip_bytes, headers={"Content-Type": "application/zip"})
    assert first.status_code == 200, first.text
    name = parse_sse_result(first)["project_name"]
    before = client.get(f"/api/projects/{name}/files/aspect/icon.svg").json()
    assert before["content_type"] == "image/svg+xml"

    downloaded = client.get(f"/api/projects/{name}")
    assert downloaded.status_code == 200, downloaded.text

    reimport = client.put("/api/projects/proj2", content=downloaded.content, headers={"Content-Type": "application/zip"})
    assert reimport.status_code == 200, reimport.text
    reimported_name = parse_sse_result(reimport)["project_name"]
    after = client.get(f"/api/projects/{reimported_name}/files/aspect/icon.svg").json()
    assert after["content_type"] == "image/svg+xml"


def test_uploading_a_project_activates_it_automatically(client):
    """Both the first upload and a later, unrelated one must activate
    the project that was just uploaded."""
    response = client.put(
        "/api/projects/first",
        content=MINIMAL_YML.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/projects").json()["active"] == "first"

    response = client.put(
        "/api/projects/second",
        content=MINIMAL_YML.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/projects").json()["active"] == "second"


def test_new_project_creates_and_activates_hello_world(client):
    """POST /api/projects creates and activates a "Hello world" project
    without requiring a name up front."""
    response = client.post("/api/projects")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_name"] == "Hello world"

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"name": "Hello world", "is_paused": False, "ui_label": "Hello, world!"}]
    assert projects["active"] == "Hello world"

    # The template's own content really is what got persisted.
    files = client.get("/api/projects/Hello world/files/index.yml").json()
    assert 'hello, world' in files["content"].lower()

    # It's actually usable, not just a stored blob, once published.
    publish_resp = client.post("/api/projects/Hello world/publish", json={})
    assert publish_resp.status_code == 200, publish_resp.text
    assert client.get("/api/chat/session").status_code == 200


class TestGetProjectsAsUser:
    """A plain 'user' only sees projects they have a UserProject row for
    (see Db.list_projects_with_availability_for_user) — every other role
    (the default test session's own "supervisor", and admin) still sees
    everything, per the tests above."""

    def test_sees_none_of_the_existing_projects_by_default(self, client):
        _upload(client, "hello", "Hello world.zip")
        _upload(client, "cat", "Aprendr català.zip")
        Session().role = "user"

        assert client.get("/api/projects").json()["projects"] == []

    def test_sees_only_a_project_they_have_access_to(self, app_db, client):
        hello = _upload(client, "hello", "Hello world.zip")
        _upload(client, "cat", "Aprendr català.zip")
        app_db.record_terms_acceptance(Session().user, hello, archive_id=None)
        Session().role = "user"

        projects = client.get("/api/projects").json()["projects"]

        assert [p["name"] for p in projects] == [hello]


def test_new_project_de_duplicates_the_name_on_repeat_calls(client):
    first = client.post("/api/projects").json()
    second = client.post("/api/projects").json()
    third = client.post("/api/projects").json()

    assert first["project_name"] == "Hello world"
    assert second["project_name"] == "Hello world 2"
    assert third["project_name"] == "Hello world 3"
    assert client.get("/api/projects").json()["projects"] == [
        {"name": "Hello world", "is_paused": False, "ui_label": "Hello, world!"},
        {"name": "Hello world 2", "is_paused": False, "ui_label": "Hello, world!"},
        {"name": "Hello world 3", "is_paused": False, "ui_label": "Hello, world!"},
    ]
