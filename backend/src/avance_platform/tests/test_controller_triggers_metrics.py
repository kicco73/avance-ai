from __future__ import annotations

import io
import zipfile

import pytest

from metrics.metrics_framework import metric_names

pytestmark = pytest.mark.regression


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def test_uploading_a_project_with_a_signal_named_after_a_metric_is_rejected(client):
    reserved = sorted(metric_names())[0]
    content = f"""
project:
  id: bad
init-action:
  target: a
signals:
  {reserved}:
    definition: "whatever"
states:
  a:
    contextual-prompt: "hi"
"""
    response = client.post("/api/projects/upload", content=_zip_of(content), headers={"Content-Type": "application/zip"})

    assert response.status_code == 400
    assert "reserved for core metrics" in response.json()["error"]["message"]
