from __future__ import annotations

import io
import logging
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


BROKEN_SIBLING = """
project:
  id: broken_sibling
  family: shared

env:
  budget:
    ui-description: "Still listed, never built."

init-action:
  target: x

states:
  x:
    contextual-prompt: "hi"
"""


def test_a_sibling_is_listed_off_its_own_text_and_never_built(client, app_db, caplog):
    """The registry names what a sibling declares — its family and its
    env keys — by reading its index.yml, never by building it: a
    sibling that no longer builds is listed all the same, stays
    available, and is reported nowhere while another project is edited."""
    project_id = _upload_and_activate(client, PROJECT)
    app_db.ensure_project("broken_sibling")
    app_db.save_project_files(
        "broken_sibling", {"index.yml": BROKEN_SIBLING.encode("utf-8")}, {"index.yml": "text/yaml"},
    )
    app_db.publish_project("broken_sibling")
    app_db.set_project_availability("broken_sibling", is_paused=False, paused_reason=None)

    with caplog.at_level(logging.WARNING):
        response = client.get(f"/api/core/projects/{project_id}/identifiers")

    assert response.status_code == 200
    body = response.json()
    assert body["event.broken_sibling"] == {"state": "The 'broken_sibling' project's own current state."}
    assert body["event.broken_sibling.env"] == {"budget": "Still listed, never built."}
    assert app_db.get_project_availability("broken_sibling") == (False, None)
    assert [record.message for record in caplog.records if "broken_sibling" in record.message] == []


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
