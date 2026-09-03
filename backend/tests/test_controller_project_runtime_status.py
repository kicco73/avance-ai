"""GET /api/settings/projects/runtime-status, PUT /api/projects/{name}/pause,
PUT /api/projects/{name}/resume (ProjectService.get_runtime_status/
set_manually_paused/set_manually_running)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.mark.regression
def test_runtime_status_lists_every_project(client, hello_project):
    response = client.get("/api/settings/projects/runtime-status")

    assert response.status_code == 200
    body = response.json()
    [row] = body["projects"]
    assert row["id"] == hello_project
    assert row["status"] == "running"
    assert row["paused_reason"] is None
    assert row["revision"] == 0
    assert row["published_revision"] == 0


@pytest.mark.regression
def test_pause_then_resume_round_trips(client, hello_project):
    response = client.put(f"/api/projects/{hello_project}/pause")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "manually_paused"

    status = client.get("/api/settings/projects/runtime-status").json()["projects"][0]
    assert status["status"] == "manually_paused"
    assert status["paused_reason"] == "Manually paused."

    response = client.put(f"/api/projects/{hello_project}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"


@pytest.mark.regression
def test_pause_rejects_a_project_that_is_not_running(client, hello_project):
    client.put(f"/api/projects/{hello_project}/pause")

    response = client.put(f"/api/projects/{hello_project}/pause")

    assert response.status_code == 400


@pytest.mark.regression
def test_resume_rejects_a_project_that_is_not_manually_paused(client, hello_project):
    response = client.put(f"/api/projects/{hello_project}/resume")

    assert response.status_code == 400


@pytest.mark.contract
def test_pause_404s_for_an_unknown_project(client):
    response = client.put("/api/projects/does-not-exist/pause")
    assert response.status_code == 404


@pytest.mark.contract
def test_resume_404s_for_an_unknown_project(client):
    response = client.put("/api/projects/does-not-exist/resume")
    assert response.status_code == 404


@pytest.mark.regression
def test_a_manually_paused_project_blocks_chat_the_same_as_an_automatic_pause(client, hello_project):
    client.put(f"/api/projects/{hello_project}/pause")

    response = client.get("/api/chat/session")

    assert response.status_code == 200
    body = response.json()
    assert body["paused"] is True
    assert body["paused_reason"] == "Manually paused."
