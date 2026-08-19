"""Regression coverage for the dedicated draft-session entry points (see
project_service.py's/chat_service.py's own revision-security hardening):
only POST /api/projects/{project_name}/test-sessions and GET .../current
may ever create a session against a revision nobody's published — every
other session entry point requires a published one, unconditionally, with
no parameter left to opt out of that.
"""
from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.regression

UNPUBLISHED_PROJECT = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
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


def test_regular_session_bootstrap_fails_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-1", UNPUBLISHED_PROJECT)

    response = client.get("/api/chat/session")

    assert response.status_code == 409
    assert "never been published" in response.json()["error"]["message"]


def test_regular_session_creation_fails_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-2", UNPUBLISHED_PROJECT)

    response = client.post("/api/chat/sessions")

    assert response.status_code == 409
    assert "never been published" in response.json()["error"]["message"]


def test_test_session_bootstrap_succeeds_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-3", UNPUBLISHED_PROJECT)

    response = client.get("/api/projects/draft-only-3/test-sessions/current")

    assert response.status_code == 200
    body = response.json()
    assert body["project_name"] == "draft-only-3"
    assert body["active"] is True


def test_post_test_session_succeeds_for_an_unpublished_project(client):
    _upload_and_activate(client, "draft-only-4", UNPUBLISHED_PROJECT)

    response = client.post("/api/projects/draft-only-4/test-sessions")

    assert response.status_code == 200
    body = response.json()
    assert body["project_name"] == "draft-only-4"


def test_allow_draft_query_param_no_longer_has_any_effect(client):
    """The whole point of the split: a caller can no longer opt into a
    draft session from the shared endpoint just by adding a query param —
    the choice is solely which endpoint is called."""
    _upload_and_activate(client, "draft-only-5", UNPUBLISHED_PROJECT)

    response = client.get("/api/chat/session?allow_draft=true")

    assert response.status_code == 409
