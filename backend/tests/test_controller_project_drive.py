"""GET .../drive and GET .../drive/{path} — the core, no-elevated-role
surface a signed-in person reads their own drive through (see
tracking.actuators.drive_namespace), the same one the customer app
detail panel's own Docs tab calls.

Core, not platform: only the fixture project is built through the
platform skill's own upload/publish pipeline (see conftest.installed_skill),
the routes under test are project_controller.py's own.
"""
from __future__ import annotations

import pytest

from conftest import chat_action, create_chat, installed_skill, parse_sse_result, run_pending_tasks, session_of

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: drive_proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "        task: |\n"
    "          drive.write('reports/last.md', 'ciao')\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _published(client) -> str:
    installed_skill("avance_platform")
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    client.post(f"/api/core/projects/{project_id}/activate")
    client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    return project_id


def test_a_task_written_file_is_listed_and_readable_over_the_core_routes(client, app):
    project_id = _published(client)
    session_id = session_of(create_chat(client, project_id, "test"))
    chat_action(client, session_id, "go")
    run_pending_tasks(app)

    listing = client.get(f"/api/core/projects/{project_id}/drive")
    assert listing.status_code == 200
    assert [f["path"] for f in listing.json()["files"]] == ["reports/last.md"]

    content = client.get(f"/api/core/projects/{project_id}/drive/reports/last.md")
    assert content.status_code == 200
    assert content.text == "ciao"
    assert content.headers["content-type"].startswith("text/markdown")


def test_reading_a_file_that_was_never_written_is_a_404(client):
    project_id = _published(client)

    response = client.get(f"/api/core/projects/{project_id}/drive/nothing.md")

    assert response.status_code == 404


def test_the_listing_is_empty_before_anything_was_ever_written(client):
    project_id = _published(client)

    response = client.get(f"/api/core/projects/{project_id}/drive")

    assert response.json() == {"files": []}
