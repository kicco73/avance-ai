"""GET /api/settings/projects/runtime-status, PUT /api/projects/{name}/pause,
PUT /api/projects/{name}/resume (ProjectService.get_runtime_status/
set_manually_paused/set_manually_running)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def _status(client) -> dict:
    response = client.get("/api/settings/projects/runtime-status")
    assert response.status_code == 200
    [row] = response.json()["projects"]
    return row


@pytest.mark.regression
def test_pause_and_resume_round_trip_through_the_runtime_status_listing_and_each_rejects_the_wrong_starting_state(client, hello_project):
    row = _status(client)
    assert row["id"] == hello_project
    assert row["status"] == "running"
    assert row["paused_reason"] is None
    assert row["revision"] == 0
    assert row["published_revision"] == 0

    assert client.put(f"/api/projects/{hello_project}/resume").status_code == 400

    response = client.put(f"/api/projects/{hello_project}/pause")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "manually_paused"
    row = _status(client)
    assert row["status"] == "manually_paused"
    assert row["paused_reason"] == "Manually paused."

    assert client.put(f"/api/projects/{hello_project}/pause").status_code == 400

    response = client.put(f"/api/projects/{hello_project}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"


def test_pause_and_resume_both_404_for_an_unknown_project(client):
    assert client.put("/api/projects/does-not-exist/pause").status_code == 404
    assert client.put("/api/projects/does-not-exist/resume").status_code == 404


@pytest.mark.regression
def test_a_manually_paused_project_blocks_chat_the_same_as_an_automatic_pause(client, hello_project):
    client.put(f"/api/projects/{hello_project}/pause")

    response = client.get("/api/chat/session")

    assert response.status_code == 200
    body = response.json()
    assert body["paused"] is True
    assert body["paused_reason"] == "Manually paused."
