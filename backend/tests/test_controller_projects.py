from __future__ import annotations

from pathlib import Path

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.regression

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _upload(client, name, sample):
    content = (SAMPLES_DIR / sample).read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text


def test_put_project_returns_a_success_payload(client):
    """The response body must reflect the created project, not be null."""
    content = (SAMPLES_DIR / "Hello world.zip").read_bytes()
    response = client.put("/api/projects/proj", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    assert parse_sse_result(response) == {"success": True, "project_name": "proj"}


def test_fresh_install_has_no_active_project(client):
    """A user with no Settings row yet has genuinely no active project,
    not a "default" that may or may not actually exist."""
    projects = client.get("/api/projects").json()
    assert projects["projects"] == []
    assert projects["active"] is None

    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()


def test_state_reports_the_configured_input_token_budget_per_session(client):
    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["input_token_budget_per_session"] == 8000


def test_deleting_the_active_project_falls_back_to_a_remaining_one(client):
    """Whatever's left after deleting the active project becomes active."""
    _upload(client, "hello", "Hello world.zip")
    _upload(client, "cat", "Aprendr català.zip")
    client.post("/api/projects/cat/publish", json={})
    client.put("/api/projects/hello/activate")

    response = client.delete("/api/projects/hello")
    assert response.status_code == 200

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"name": "cat", "is_paused": False, "ui_label": None}]
    assert projects["active"] == "cat"

    # The fallback must actually be activated, not just recorded by name.
    assert client.get("/api/chat/session").status_code == 200


def test_deleting_the_last_project_does_not_crash(client):
    _upload(client, "hello", "Hello world.zip")
    client.put("/api/projects/hello/activate")

    response = client.delete("/api/projects/hello")
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
    _upload(client, "default", "Hello world.zip")

    response = client.delete("/api/projects/default")

    assert response.status_code == 200
    assert client.get("/api/projects").json()["projects"] == []


MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


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
