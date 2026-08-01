from __future__ import annotations

from pathlib import Path

EXPECTED_METRIC_NAMES = {"engagement", "retention", "activity_consistency", "state_stability", "signal_stability"}


def test_metrics_endpoint_returns_every_core_metric_with_ui_metadata(client, hello_project):
    response = client.get("/api/chat/metrics")

    assert response.status_code == 200
    body = response.json()
    assert {m["name"] for m in body} == EXPECTED_METRIC_NAMES
    for metric in body:
        assert isinstance(metric["ui_label"], str) and metric["ui_label"]
        assert isinstance(metric["ui_description"], str) and metric["ui_description"]
        assert 0.0 <= metric["value"] <= 100.0


def test_metrics_reflect_an_empty_conversation_at_baseline(client, hello_project):
    # Bootstrapping alone (no messages sent yet) still creates one session,
    # so engagement's session component is non-zero — only its message
    # component (and every message-driven metric) stays at the floor.
    client.get("/api/chat/session")

    body = client.get("/api/chat/metrics").json()
    by_name = {m["name"]: m["value"] for m in body}

    assert by_name["activity_consistency"] == 0.0
    assert by_name["retention"] == 0.0
    assert by_name["signal_stability"] == 0.0


def test_engagement_rises_after_sending_messages(client, hello_project):
    session = client.get("/api/chat/session").json()
    baseline = {m["name"]: m["value"] for m in client.get("/api/chat/metrics").json()}["engagement"]

    for text in ("hi", "how are you", "tell me more"):
        response = client.post("/api/chat/messages", json={"message": text, "session_id": session["id"]})
        assert response.status_code == 200

    after = {m["name"]: m["value"] for m in client.get("/api/chat/metrics").json()}["engagement"]
    assert after > baseline


def test_metrics_are_scoped_to_the_active_project(client):
    samples_dir = Path(__file__).resolve().parent.parent / "samples"
    for name, sample in (("hello", "Hello world.zip"), ("cat", "Aprendr català.zip")):
        content = (samples_dir / sample).read_bytes()
        resp = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 200, resp.text

    client.put("/api/projects/hello/activate")
    session = client.get("/api/chat/session").json()
    for text in ("hi", "again", "and again"):
        client.post("/api/chat/messages", json={"message": text, "session_id": session["id"]})
    hello_engagement = {m["name"]: m["value"] for m in client.get("/api/chat/metrics").json()}["engagement"]

    client.put("/api/projects/cat/activate")
    cat_engagement = {m["name"]: m["value"] for m in client.get("/api/chat/metrics").json()}["engagement"]

    assert hello_engagement > 0.0
    assert cat_engagement == 0.0
