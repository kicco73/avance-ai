"""A legacy index.yml is rewritten where it enters the system (upload)
and where a stored revision fails to build (AutomatonLoader via
StoredIndexYml); the design view has no call of its own for it."""
from __future__ import annotations

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract

LEGACY_YML = """\
project:
  id: legacy_talk
  ui-label: Legacy
  talk-enabled: true
init-action:
  target: a
states:
  a:
    ui-label: A
    input-processor: ai
    contextual-prompt: hi
"""


def _upload(client, yml: str) -> str:
    response = client.post(
        "/api/skills/platform/projects/upload", content=yml.encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    return parse_sse_result(response)["project_id"]


def _index_yml(client, project_id: str) -> str:
    return client.get(f"/api/skills/platform/projects/{project_id}/files/index.yml").json()["content"]


def test_a_legacy_file_is_rewritten_on_the_way_in_and_builds(client):
    project_id = _upload(client, LEGACY_YML)

    assert "talk-enabled" not in _index_yml(client, project_id)
    assert "talk: required" in _index_yml(client, project_id)
    assert client.get(f"/api/skills/platform/projects/{project_id}/graph").status_code == 200


def test_there_is_no_modernize_route(client, hello_project):
    response = client.post(f"/api/skills/platform/projects/{hello_project}/index-yml/modernize")

    assert response.status_code in (404, 405)


def test_the_project_keeps_saying_what_it_said(client):
    project_id = _upload(client, LEGACY_YML)

    assert client.get(f"/api/skills/platform/projects/{project_id}/project").json()["project"]["services"] == {
        "talk": "required",
    }
