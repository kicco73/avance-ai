from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.regression

PROJECT = """
project:
  id: identifiers_proj
  family: shared

init-action:
  target: a

env:
  visits:
    type: number
    ui-description: "How many times this action has fired."

states:
  a:
    contextual-prompt: "hi"
"""

OTHER_PROJECT = """
project:
  id: other_proj
  family: shared

init-action:
  target: x

env:
  budget:
    type: number
    ui-description: "Remaining shared budget."

states:
  x:
    contextual-prompt: "hi"
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload_and_activate(client, yaml_text: str) -> str:
    response = client.post(
        "/api/skills/platform/projects/upload", content=_zip_of(yaml_text), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.post(f"/api/core/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


def test_event_namespace_lists_every_other_same_family_project_never_the_active_one(client):
    other_id = _upload_and_activate(client, OTHER_PROJECT)
    project_id = _upload_and_activate(client, PROJECT)

    response = client.get(f"/api/core/projects/{project_id}/identifiers")

    assert response.status_code == 200
    body = response.json()
    assert body["event"] == {}
    assert f"event.{project_id}" not in body
    assert body[f"event.{other_id}"] == {"state": f"The '{other_id}' project's own current state."}
    assert body[f"event.{other_id}.env"] == {"budget": "Remaining shared budget."}
