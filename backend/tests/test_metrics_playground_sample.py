"""Exercises samples/Metrics Playground.zip, a project that declares
triggers over every core metric.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _upload_and_activate(client, name: str = "metrics-playground"):
    content = (SAMPLES_DIR / "Metrics Playground.zip").read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    response = client.put(f"/api/projects/{name}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{name}/publish", json={})
    assert response.status_code == 200, response.text
    return name


def _metric_values(client) -> dict[str, float]:
    return {m["name"]: m["value"] for m in client.get("/api/projects/metrics-playground/metrics").json()}


@pytest.mark.contract
def test_the_sample_loads_and_starts_at_lobby(client):
    _upload_and_activate(client)

    session = client.get("/api/chat/session").json()

    assert session["start_state"] == "lobby"


@pytest.mark.contract
def test_metric_values_never_include_a_non_session_scoped_metric(client):
    """retention/activity_consistency's scope excludes one_session, the
    only context a chat turn's trigger evaluation runs in — so neither
    metric appears here."""
    _upload_and_activate(client)
    client.get("/api/chat/session")

    values = _metric_values(client)

    assert "retention" not in values
    assert "activity_consistency" not in values
