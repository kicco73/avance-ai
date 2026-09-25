"""GET .../drive and GET .../drive/{path} — the core, no-elevated-role
surface a signed-in person reads their own drive through (see
tracking.actuators.drive_namespace), the same one the customer app
detail panel's own Docs tab calls. Also POST .../drive/save-media and
POST .../drive/download-media — the "customer"-and-up, role-gated
surface behind the PDF preview dialog's Save/Download buttons.

Core, not platform: only the fixture project is built through the
platform skill's own upload/publish pipeline (see conftest.installed_skill),
the routes under test are project_controller.py's own.
"""
from __future__ import annotations

import pytest

from conftest import RecordedMessages, chat_action, create_chat, installed_skill, parse_sse_result, run_pending_tasks, session_of
from project.project_controller import ProjectController
from system.bus import OUTPUT_DRIVE

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: drive_proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    input-processor: ai\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "        task: |\n"
    "          drive.write('reports/last.md', 'ciao')\n"
    "  b:\n"
    "    input-processor: ai\n"
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


def _published_with_media(app_db, project_id: str = "drive_media_proj") -> str:
    app_db.get_or_create_user("test", "sub-user", "user", "user", None)
    app_db.ensure_project(project_id)
    app_db.save_project_files(
        project_id,
        {"index.yml": YML.replace("drive_proj", project_id).encode("utf-8"), "media/report.pdf": b"%PDF-1.4 fake"},
        {"index.yml": "text/yaml", "media/report.pdf": "application/pdf"},
    )
    app_db.publish_project(project_id)
    return project_id


def test_save_media_adds_the_project_file_to_the_callers_drive_without_counting_a_download(client, app_db):
    project_id = _published_with_media(app_db)
    recorded = RecordedMessages(OUTPUT_DRIVE)

    response = client.post(f"/api/core/projects/{project_id}/drive/save-media", json={"file_name": "media/report.pdf"})

    assert response.status_code == 200, response.text
    assert response.json() == {"path": "media/report.pdf", "downloads": 0}
    content = client.get(f"/api/core/projects/{project_id}/drive/media/report.pdf")
    assert content.status_code == 200
    assert content.content == b"%PDF-1.4 fake"
    (message,) = recorded.of_type(OUTPUT_DRIVE)
    assert message.project_id == project_id
    assert message.body == {"path": "media/report.pdf"}


def test_download_media_saves_it_too_and_counts_every_call(client, app_db):
    project_id = _published_with_media(app_db)

    first = client.post(f"/api/core/projects/{project_id}/drive/download-media", json={"file_name": "media/report.pdf"})
    second = client.post(f"/api/core/projects/{project_id}/drive/download-media", json={"file_name": "media/report.pdf"})

    assert first.json() == {"path": "media/report.pdf", "downloads": 1}
    assert second.json() == {"path": "media/report.pdf", "downloads": 2}
    assert client.get(f"/api/core/projects/{project_id}/drive/media/report.pdf").content == b"%PDF-1.4 fake"


def test_saving_after_downloading_does_not_reset_the_counter(client, app_db):
    project_id = _published_with_media(app_db)
    client.post(f"/api/core/projects/{project_id}/drive/download-media", json={"file_name": "media/report.pdf"})

    response = client.post(f"/api/core/projects/{project_id}/drive/save-media", json={"file_name": "media/report.pdf"})

    assert response.json() == {"path": "media/report.pdf", "downloads": 1}


def test_deleting_a_file_removes_it_from_the_callers_drive_and_announces_it(client, app_db):
    project_id = _published_with_media(app_db)
    client.post(f"/api/core/projects/{project_id}/drive/save-media", json={"file_name": "media/report.pdf"})
    recorded = RecordedMessages(OUTPUT_DRIVE)

    response = client.delete(f"/api/core/projects/{project_id}/drive/media/report.pdf")

    assert response.status_code == 200, response.text
    assert client.get(f"/api/core/projects/{project_id}/drive").json() == {"files": []}
    assert client.get(f"/api/core/projects/{project_id}/drive/media/report.pdf").status_code == 404
    (message,) = recorded.of_type(OUTPUT_DRIVE)
    assert message.body == {"path": "media/report.pdf"}


def test_deleting_a_file_that_is_not_there_is_a_404(client):
    project_id = _published(client)

    response = client.delete(f"/api/core/projects/{project_id}/drive/nothing.md")

    assert response.status_code == 404


def test_save_media_and_download_media_require_at_least_customer():
    """The `app`/`client` fixtures never wire AuthMiddleware in (see
    auth.tests.test_auth_middleware for where that gate itself is
    tested), so the route declaration a real deployment's middleware
    reads (getattr(endpoint, "__required_role__")) is what's checked
    here directly."""
    assert ProjectController.post_save_media_to_drive.__required_role__ == "customer"
    assert ProjectController.post_download_media_to_drive.__required_role__ == "customer"
    assert ProjectController.delete_drive_file.__required_role__ == "customer"
