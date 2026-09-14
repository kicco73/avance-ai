"""POST /api/skills/platform/projects/{project_id}/index-yml/modernize —
what "Edit project" calls on open. It rewrites every deprecated spelling
today's format states exactly (automaton/deprecations.py), saves the
file, and answers what it changed so the view can say so. A build made
before it ran still reports those spellings as warnings, which is how
the two halves stay in step: what the fixer rewrites is what stops being
warned about, and what it does not know stays warned about.
"""
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


def _warnings(client, project_id: str) -> list[str]:
    return [
        warning["message"]
        for warning in client.get(f"/api/skills/platform/projects/{project_id}/graph").json()["build_warnings"]
    ]


def test_a_deprecated_spelling_is_rewritten_saved_and_reported(client):
    project_id = _upload(client, LEGACY_YML)
    assert _warnings(client, project_id) == [
        "project.talk-enabled is deprecated — write 'services: {talk: required}' instead."
    ]

    response = client.post(f"/api/skills/platform/projects/{project_id}/index-yml/modernize")

    assert response.status_code == 200, response.text
    assert response.json()["fixed"] == ["project.talk-enabled → services: {talk: required}"]
    assert "talk-enabled" not in _index_yml(client, project_id)
    assert "talk: required" in _index_yml(client, project_id)
    assert _warnings(client, project_id) == []


def test_opening_a_project_with_nothing_to_fix_changes_nothing(client, hello_project):
    before = _index_yml(client, hello_project)

    response = client.post(f"/api/skills/platform/projects/{hello_project}/index-yml/modernize")

    assert response.status_code == 200
    assert response.json()["fixed"] == []
    assert _index_yml(client, hello_project) == before


def test_the_project_keeps_saying_what_it_said(client):
    project_id = _upload(client, LEGACY_YML)

    client.post(f"/api/skills/platform/projects/{project_id}/index-yml/modernize")

    assert client.get(f"/api/skills/platform/projects/{project_id}/project").json()["project"]["services"] == {
        "talk": "required",
    }
