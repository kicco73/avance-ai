from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _upload(client, name, sample):
    content = (SAMPLES_DIR / sample).read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text


def test_put_project_returns_a_success_payload(client):
    """Regression test: PUT /api/projects/{project_name} used to return
    nothing at all (a missing `return result`, see SettingsController.
    put_project) — the request succeeded and the project was actually
    created, but the response body was always `null`."""
    content = (SAMPLES_DIR / "Hello world.zip").read_bytes()
    response = client.put("/api/projects/proj", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    assert response.json() == {"success": True, "project_name": "proj"}


def test_fresh_install_has_no_active_project(client):
    """Regression test: no project name is reserved/defaulted-to anymore
    (see ProjectService.get_active_project_name) — a user with no
    Settings row yet has genuinely no active project, not a "default"
    that may or may not actually exist."""
    projects = client.get("/api/projects").json()
    assert projects["projects"] == []
    assert projects["active"] is None

    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()


def test_deleting_the_active_project_falls_back_to_a_remaining_one(client):
    """No project name is preferred for continuity — whatever's left
    after deleting the active one becomes active instead."""
    _upload(client, "hello", "Hello world.zip")
    _upload(client, "cat", "Aprendr català.zip")
    client.post("/api/projects/cat/publish", json={})
    client.put("/api/projects/hello/activate")

    response = client.delete("/api/projects/hello")
    assert response.status_code == 200

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"name": "cat", "is_paused": False, "ui_label": None}]
    assert projects["active"] == "cat"

    # The fallback must have actually activated it, not just recorded the
    # name — a normal chat bootstrap must work against it.
    assert client.get("/api/chat/session").status_code == 200


def test_deleting_the_last_project_does_not_crash(client):
    _upload(client, "hello", "Hello world.zip")
    client.put("/api/projects/hello/activate")

    response = client.delete("/api/projects/hello")
    assert response.status_code == 200

    # No project left at all — GET /api/state must degrade gracefully
    # rather than error out trying to load a nonexistent active project.
    state = client.get("/api/state")
    assert state.status_code == 200
    assert "key" not in state.json()

    projects = client.get("/api/projects").json()
    assert projects["active"] is None


def test_default_project_can_be_deleted(client):
    """"default" is just a name, like any other — not specially protected
    from deletion (see ProjectService.delete_project's own fallback logic,
    which no longer prefers it either)."""
    _upload(client, "default", "Hello world.zip")

    response = client.delete("/api/projects/default")

    assert response.status_code == 200
    assert client.get("/api/projects").json()["projects"] == []


MINIMAL_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def test_uploading_a_bare_yaml_file_creates_a_single_file_project(client):
    """Regression test: PUT /api/projects/{name} documents accepting "YAML
    or zip" (see ProjectService.put_project/_looks_like_zip), but used to
    hard-reject anything that wasn't a zip outright — rejecting exactly
    the bare-.yml upload the frontend's own "Upload project..." picker
    (accept=".zip,.yml,.yaml") invites."""
    response = client.put(
        "/api/projects/bare",
        content=MINIMAL_YML.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )

    assert response.status_code == 200, response.text
    body = client.get("/api/projects/bare/files/index.yml").json()
    assert body["content"] == MINIMAL_YML


def test_uploading_a_project_activates_it_automatically(client):
    """Both the very first upload (nothing was active before) and a
    second, unrelated one (something else was active) must land on the
    project that was just uploaded."""
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
    """POST /api/projects is the "New project" menu option — same
    effect as uploading samples/Hello world.zip by hand, minus picking a
    name first."""
    response = client.post("/api/projects")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_name"] == "Hello world"

    projects = client.get("/api/projects").json()
    assert projects["projects"] == [{"name": "Hello world", "is_paused": False, "ui_label": None}]
    assert projects["active"] == "Hello world"

    # The template's own content really is what got persisted.
    files = client.get("/api/projects/Hello world/files/index.yml").json()
    assert 'hello, world' in files["content"].lower()

    # And it's actually usable, not just a stored blob — once published
    # (see Db.create_chat_session, a project can't have chat sessions
    # before its first publish).
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
        {"name": "Hello world", "is_paused": False, "ui_label": None},
        {"name": "Hello world 2", "is_paused": False, "ui_label": None},
        {"name": "Hello world 3", "is_paused": False, "ui_label": None},
    ]
