"""Integration tests for the index.css "skin" feature's backend surface:
text-editable CSS, image attachments (Content-Type-gated, capped at
MAX_IMAGE_UPLOAD_BYTES), and the raw GET .../files/{file_name}/content
route, which resolves its revision from `session_id` the same way the
automaton itself does for that session.
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result
from project.archive.layout import MAX_IMAGE_UPLOAD_BYTES

pytestmark = pytest.mark.contract

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"0" * 32

TWO_STATE_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        ui-label: Go\n"
    "        ui-button: Go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _put_png(client, project_id: str, name: str = "aspect/logo.png", content: bytes = PNG_MAGIC, content_type: str | None = "image/png"):
    headers = {"Content-Type": content_type} if content_type else {}
    return client.put(f"/api/projects/{project_id}/files/{name}", content=content, headers=headers)


def _put_css(client, project_id: str, css: bytes):
    return client.put(f"/api/projects/{project_id}/files/index.css", content=css)


def _publish_two_state_project_with_red_css(client) -> None:
    client.post("/api/projects/upload", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
    _put_css(client, "proj", b"body { color: red; }")
    client.post("/api/projects/proj/publish", json={})


@pytest.mark.regression
def test_get_project_file_reports_content_type_and_real_byte_size_with_content_only_for_text_files(client, hello_project):
    """Unlike content (null for binary files, since raw bytes aren't
    JSON-serializable — see _file_undo_redo_info's own comment), size is
    always the real byte count either way."""
    css = b"body { color: red; }"
    assert _put_css(client, hello_project, css).status_code == 200

    response = client.get(f"/api/projects/{hello_project}/files/index.css")
    assert response.status_code == 200
    assert response.json()["content_type"] == "text/css"
    assert response.json()["size"] == len(css)

    assert _put_png(client, hello_project).status_code == 200

    response = client.get(f"/api/projects/{hello_project}/files/aspect/logo.png")
    assert response.status_code == 200
    body = response.json()
    assert body["content_type"] == "image/png"
    assert body["content"] is None
    assert body["size"] == len(PNG_MAGIC)


@pytest.mark.regression
def test_a_missing_index_css_is_204_no_content_while_any_other_missing_file_is_404(client, hello_project):
    """index.css is the one file every project is allowed not to have —
    missing, this is 204 No Content, not a 404, so an editor can start
    with an empty buffer instead of surfacing an error."""
    yml = TWO_STATE_YML.replace("id: proj", "id: no_css")
    response = client.post("/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]

    response = client.get(f"/api/projects/{project_id}/files/index.css")
    assert response.status_code == 204
    assert response.content == b""

    assert client.get(f"/api/projects/{hello_project}/files/does-not-exist.txt").status_code == 404


@pytest.mark.regression
def test_an_aspect_image_upload_is_readable_back_raw_by_its_bare_name_too(client, hello_project):
    assert _put_png(client, hello_project).status_code == 200

    response = client.get(f"/api/projects/{hello_project}/files/logo.png/content")
    assert response.status_code == 200
    assert response.content == PNG_MAGIC
    assert response.headers["content-type"] == "image/png"


@pytest.mark.regression
def test_image_upload_rejects_a_bare_name_for_a_new_file_a_mismatched_or_missing_content_type_and_an_oversized_file(client, hello_project):
    bare = _put_png(client, hello_project, name="logo.png")
    assert bare.status_code == 400
    assert "aspect/logo.png" in bare.json()["error"]["message"]

    assert _put_png(client, hello_project, content_type="image/jpeg").status_code == 400
    assert _put_png(client, hello_project, content_type=None).status_code == 400
    assert _put_png(client, hello_project, content=b"0" * (MAX_IMAGE_UPLOAD_BYTES + 1)).status_code == 400


@pytest.mark.regression
def test_index_css_save_rejects_a_missing_relative_reference_but_accepts_existing_and_absolute_ones(client, hello_project):
    missing = _put_css(client, hello_project, b"body { background: url(missing-bg.png); }")
    assert missing.status_code == 400
    assert "missing-bg.png" in missing.json()["error"]["message"]

    assert _put_png(client, hello_project, name="aspect/bg.png").status_code == 200
    assert _put_css(client, hello_project, b"body { background: url('./bg.png'); }").status_code == 200
    assert _put_css(client, hello_project, b"body { background: url(https://example.com/bg.png); }").status_code == 200


class TestGetProjectFileContent:
    def test_serves_the_current_draft_with_an_etag_that_round_trips_to_a_304_and_404s_an_unknown_file(self, client, hello_project):
        assert client.get(f"/api/projects/{hello_project}/files/does-not-exist.png/content").status_code == 404

        _put_css(client, hello_project, b"body { color: red; }")
        _put_css(client, hello_project, b"body { color: blue; }")

        first = client.get(f"/api/projects/{hello_project}/files/index.css/content")
        assert first.status_code == 200
        assert first.content == b"body { color: blue; }"
        etag = first.headers["etag"]

        second = client.get(f"/api/projects/{hello_project}/files/index.css/content", headers={"If-None-Match": etag})
        assert second.status_code == 304

    def test_a_live_session_stays_pinned_to_its_own_published_revision(self, client):
        # A two-state project with a real action: firing it establishes a
        # current_state reliably, unlike hello_project's single-state one.
        _publish_two_state_project_with_red_css(client)

        session_response = client.get("/api/chat/session")
        assert session_response.status_code == 200, session_response.text
        session_id = session_response.json()["id"]
        action_response = client.post(f"/api/chat/sessions/{session_id}/action", json={"action_name": "go"})
        assert action_response.status_code == 200, action_response.text

        # A later edit + publish moves the draft/published revision ahead —
        # the already-created session must keep seeing revision 1.
        _put_css(client, "proj", b"body { color: blue; }")
        client.post("/api/projects/proj/publish", json={})

        pinned = client.get(f"/api/projects/proj/files/index.css/content?session_id={session_id}")
        current = client.get("/api/projects/proj/files/index.css/content")

        assert pinned.content == b"body { color: red; }"
        assert current.content == b"body { color: blue; }"

    def test_a_test_session_always_tracks_the_live_draft(self, client):
        _publish_two_state_project_with_red_css(client)

        test_session_response = client.post("/api/projects/proj/test-sessions")
        assert test_session_response.status_code == 200, test_session_response.text
        test_session_id = test_session_response.json()["id"]
        action_response = client.post(f"/api/chat/sessions/{test_session_id}/action", json={"action_name": "go"})
        assert action_response.status_code == 200, action_response.text

        # Edited *after* the Test session was already open — a 'test'
        # session must still see this, unlike a live/native one.
        _put_css(client, "proj", b"body { color: green; }")

        response = client.get(f"/api/projects/proj/files/index.css/content?session_id={test_session_id}")

        assert response.content == b"body { color: green; }"
