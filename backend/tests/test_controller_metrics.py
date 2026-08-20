from __future__ import annotations

from pathlib import Path

import pytest

# retention/activity_consistency are scoped to {all_sessions_per_user,
# all_sessions}, so they're excluded from the one_session context here.
EXPECTED_METRIC_NAMES = {"engagement", "state_stability", "signal_stability"}


@pytest.mark.contract
def test_metrics_endpoint_returns_every_core_metric_with_ui_metadata(client, hello_project):
    response = client.get("/api/projects/hello/metrics")

    assert response.status_code == 200
    body = response.json()
    assert {m["name"] for m in body} == EXPECTED_METRIC_NAMES
    for metric in body:
        assert isinstance(metric["ui_label"], str) and metric["ui_label"]
        assert isinstance(metric["ui_description"], str) and metric["ui_description"]
        assert 0.0 <= metric["value"] <= 100.0


@pytest.mark.regression
def test_metrics_reflect_an_empty_conversation_at_baseline(client, hello_project):
    # Bootstrapping alone creates a session, so only message-driven
    # metrics like signal_stability stay at the floor.
    client.get("/api/chat/session")

    body = client.get("/api/projects/hello/metrics").json()
    by_name = {m["name"]: m["value"] for m in body}

    assert by_name["signal_stability"] == 0.0


@pytest.mark.regression
def test_engagement_rises_after_sending_messages(client, hello_project):
    session = client.get("/api/chat/session").json()
    baseline = {m["name"]: m["value"] for m in client.get("/api/projects/hello/metrics").json()}["engagement"]

    for text in ("hi", "how are you", "tell me more"):
        response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": text})
        assert response.status_code == 200

    after = {m["name"]: m["value"] for m in client.get("/api/projects/hello/metrics").json()}["engagement"]
    assert after > baseline


@pytest.mark.contract
def test_metrics_are_scoped_to_the_url_project(client):
    samples_dir = Path(__file__).resolve().parent.parent / "samples" / "projects"
    for name, sample in (("hello", "Hello world.zip"), ("cat", "Aprendr català.zip")):
        content = (samples_dir / sample).read_bytes()
        resp = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 200, resp.text
        resp = client.post(f"/api/projects/{name}/publish", json={})
        assert resp.status_code == 200, resp.text

    client.put("/api/projects/hello/activate")
    session = client.get("/api/chat/session").json()
    for text in ("hi", "again", "and again"):
        client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": text})
    hello_engagement = {m["name"]: m["value"] for m in client.get("/api/projects/hello/metrics").json()}["engagement"]

    client.put("/api/projects/cat/activate")
    cat_engagement = {m["name"]: m["value"] for m in client.get("/api/projects/cat/metrics").json()}["engagement"]

    assert hello_engagement > 0.0
    assert cat_engagement == 0.0
