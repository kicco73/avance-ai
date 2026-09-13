"""GET /api/skills/platform/settings/projects/runtime-status, PUT /api/skills/platform/projects/{name}/pause,
PUT /api/skills/platform/projects/{name}/resume (ProjectService.get_runtime_status/
set_manually_paused/set_manually_running)."""
from __future__ import annotations

import pytest

from conftest import enter_chat

pytestmark = pytest.mark.contract


def _status(client) -> dict:
    response = client.get("/api/skills/platform/settings/projects/runtime-status")
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

    assert client.post(f"/api/skills/platform/projects/{hello_project}/resume").status_code == 400

    response = client.post(f"/api/skills/platform/projects/{hello_project}/pause")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "manually_paused"
    row = _status(client)
    assert row["status"] == "manually_paused"
    assert row["paused_reason"] == "Manually paused."

    assert client.post(f"/api/skills/platform/projects/{hello_project}/pause").status_code == 400

    response = client.post(f"/api/skills/platform/projects/{hello_project}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"


def test_pause_and_resume_both_404_for_an_unknown_project(client):
    assert client.post("/api/skills/platform/projects/does-not-exist/pause").status_code == 404
    assert client.post("/api/skills/platform/projects/does-not-exist/resume").status_code == 404


@pytest.mark.regression
def test_a_manually_paused_project_blocks_chat_the_same_as_an_automatic_pause(client, hello_project):
    client.post(f"/api/skills/platform/projects/{hello_project}/pause")

    blocked = enter_chat(client, hello_project)[-1]

    assert blocked["type"] == "session.blocked"
    assert blocked["reason"] == "paused"
    assert blocked["detail"] == "Manually paused."
