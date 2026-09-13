"""Exercises samples/Metrics Playground.zip, a project that declares
triggers over every core metric.
"""
from __future__ import annotations

import pytest

from conftest import enter_chat, parse_sse_result

from conftest import SAMPLES_DIR


def _upload_and_activate(client):
    content = (SAMPLES_DIR / "Metrics Playground.zip").read_bytes()
    response = client.post("/api/skills/platform/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.post(f"/api/skills/platform/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


def _metric_values(client, project_id: str) -> dict[str, float]:
    return {m["name"]: m["value"] for m in client.get(f"/api/core/projects/{project_id}/metrics").json()}


@pytest.mark.contract
def test_the_sample_loads_and_starts_at_lobby(client):
    project_id = _upload_and_activate(client)

    info = next(frame for frame in enter_chat(client, project_id) if frame["type"] == "session.info")

    assert info["state"]["key"] == "lobby"


@pytest.mark.contract
def test_metric_values_never_include_a_non_session_scoped_metric(client):
    """retention/activity_consistency's scope excludes one_session, the
    only context a chat turn's trigger evaluation runs in — so neither
    metric appears here."""
    project_id = _upload_and_activate(client)
    enter_chat(client, project_id)

    values = _metric_values(client, project_id)

    assert "retention" not in values
    assert "activity_consistency" not in values
