from __future__ import annotations

import io
import zipfile

import pytest

from metrics.metrics_framework import metric_names

pytestmark = pytest.mark.regression

METRIC_TRIGGER_PROJECT = """
init-action:
  target: a

signals:
  myOwnSignal:
    definition: "whatever"

states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "engagement >= 1"
  b:
    contextual-prompt: "bye"
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload_and_activate(client, name: str, yaml_text: str):
    response = client.put(
        f"/api/projects/{name}", content=_zip_of(yaml_text), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    response = client.put(f"/api/projects/{name}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{name}/publish", json={})
    assert response.status_code == 200, response.text


def test_uploading_a_project_with_a_signal_named_after_a_metric_is_rejected(client):
    reserved = sorted(metric_names())[0]
    content = f"""
init-action:
  target: a
signals:
  {reserved}:
    definition: "whatever"
states:
  a:
    contextual-prompt: "hi"
"""
    response = client.put("/api/projects/bad", content=_zip_of(content), headers={"Content-Type": "application/zip"})

    assert response.status_code == 400
    assert "reserved for core metrics" in response.json()["error"]["message"]


def test_triggers_preview_merges_metric_values_when_referenced(client):
    _upload_and_activate(client, "metric-trigger", METRIC_TRIGGER_PROJECT)
    # A freshly bootstrapped session already scores "engagement" above zero
    # via its own session component (see test_controller_metrics.py) — the
    # preview must reflect that even though only a *signal* value is sent.
    client.get("/api/chat/session")

    response = client.post("/api/triggers/preview", json={"signals": {"myOwnSignal": 10}})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["action_name"] == "advance"
    assert body[0]["trigger"] == "engagement >= 1"
    assert body[0]["result"] is True
    assert body[0]["would_fire"] is True


def test_triggers_preview_does_not_fire_when_the_metric_threshold_is_not_met(client):
    high_threshold_project = METRIC_TRIGGER_PROJECT.replace("engagement >= 1", "engagement >= 99")
    _upload_and_activate(client, "metric-trigger-high", high_threshold_project)
    client.get("/api/chat/session")

    response = client.post("/api/triggers/preview", json={"signals": {"myOwnSignal": 10}})

    assert response.status_code == 200
    body = response.json()
    assert body[0]["result"] is False
    assert body[0]["would_fire"] is False


def test_triggers_preview_skips_metrics_entirely_when_nothing_references_one(client, hello_project):
    # "Hello world" has no actions/triggers at all in its only state.
    response = client.post("/api/triggers/preview", json={"signals": {}})

    assert response.status_code == 200
    assert response.json() == []
