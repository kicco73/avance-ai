from __future__ import annotations

from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def _upload(client, name, sample):
    content = (SAMPLES_DIR / sample).read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text


def test_deleting_the_active_project_falls_back_to_a_remaining_one(client):
    """Regression test: DEFAULT_PROJECT_NAME ("default") is just a
    reserved name, not guaranteed to actually exist as an uploaded
    project. Deleting the active project used to always try to activate
    "default" unconditionally and 404 when it wasn't there — it must now
    fall back to whatever project is actually left."""
    _upload(client, "hello", "Hello world.zip")
    _upload(client, "cat", "Aprendr català.zip")
    client.put("/api/projects/hello/activate")

    response = client.delete("/api/projects/hello")
    assert response.status_code == 200

    projects = client.get("/api/projects").json()
    assert projects["projects"] == ["cat"]
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


def test_default_project_cannot_be_deleted(client):
    _upload(client, "default", "Hello world.zip")

    response = client.delete("/api/projects/default")

    assert response.status_code == 403
