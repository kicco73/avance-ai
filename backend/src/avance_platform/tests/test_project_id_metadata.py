"""Tests for project.id (mandatory, globally unique, a plain identifier,
the sole identity a project is known by).
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result
from db.db import Db

pytestmark = pytest.mark.contract

MINIMAL = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _upload(client, yml: str):
    return client.post("/api/skills/platform/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})



@pytest.mark.parametrize(("yml", "mentions"), [
    (MINIMAL, "project.id"),
    ("project:\n  id: not-valid\n" + MINIMAL, "project.id"),
], ids=["missing-id", "invalid-identifier"])
def test_project_id_is_required_and_must_be_a_valid_identifier(client, yml, mentions):
    resp = _upload(client, yml)
    assert resp.status_code == 400
    assert mentions in resp.json()["error"]["message"]


def test_an_upload_persists_the_declared_project_and_re_uploading_publishes_a_new_revision_only_when_it_is_newer(client, app_db: Db):
    resp = _upload(client, "project:\n  id: proj_one\n  ui-label: Project One\n  ui-description: The first one.\n" + MINIMAL)
    assert resp.status_code == 200, resp.text
    assert parse_sse_result(resp)["project_id"] == "proj_one"
    assert app_db.project_exists("proj_one")

    assert _upload(client, "project:\n  id: stable\n" + MINIMAL).status_code == 200
    assert app_db.get_project_published_revision("stable") == 0
    assert _upload(client, "project:\n  id: stable\n" + MINIMAL.replace("hi", "hi again")).status_code == 200
    assert app_db.get_project_published_revision("stable") == 1

    assert _upload(client, "project:\n  id: stable2\n  revision: 5\n" + MINIMAL).status_code == 200
    assert app_db.get_project_published_revision("stable2") == 5
    resp = _upload(client, "project:\n  id: stable2\n  revision: 5\n" + MINIMAL)
    assert resp.status_code == 400
    assert "not newer" in resp.json()["error"]["message"]
    assert _upload(client, "project:\n  id: stable2\n  revision: 9\n" + MINIMAL).status_code == 200
    assert app_db.get_project_published_revision("stable2") == 9


def test_changing_a_projects_id_through_the_editor_frees_up_the_old_one(client):
    assert _upload(client, "project:\n  id: old_id\n" + MINIMAL).status_code == 200

    resp = client.put(
        "/api/skills/platform/projects/old_id/files/index.yml",
        content=("project:\n  id: new_id\n" + MINIMAL).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code == 200, resp.text

    assert _upload(client, "project:\n  id: old_id\n" + MINIMAL).status_code == 200

