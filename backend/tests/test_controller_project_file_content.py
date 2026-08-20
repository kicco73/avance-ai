"""Integration tests for the index.css "skin" feature's backend surface:

- .css is now a text-editable extension, with its own persisted
  content_type (project.project_service.TEXT_EDITABLE_EXTENSIONS).
- Image attachments (project.project_service.IMAGE_EXTENSIONS) can be
  written through the same PUT .../files/{file_name} route, gated by a
  Content-Type header that must match the extension, and capped at
  MAX_IMAGE_UPLOAD_BYTES.
- index.css's own url(...) references are validated against the
  project's current archives at save time (missing_css_references).
- The new raw GET .../files/{file_name}/content route serves bytes
  directly (for ChatWindow.vue's own skin injection and the file
  explorer's image preview), resolving its revision from `session_id`
  exactly the way ProjectService.get_automaton_and_state_for_session
  resolves the automaton for that same session — a live/native session
  pinned to its own project_revision, a 'test' session always tracking
  whatever the current draft is.
"""
from __future__ import annotations

import pytest

from project.project_service import MAX_IMAGE_UPLOAD_BYTES

pytestmark = pytest.mark.contract

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"0" * 32

TWO_STATE_YML = (
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


@pytest.mark.regression
def test_content_type_surfaces_on_get_project_file(client, hello_project):
    response = client.put(f"/api/projects/{hello_project}/files/index.css", content=b"body { color: red; }")
    assert response.status_code == 200, response.text

    response = client.get(f"/api/projects/{hello_project}/files/index.css")
    assert response.status_code == 200
    assert response.json()["content_type"] == "text/css"


@pytest.mark.regression
def test_get_project_file_reports_no_content_for_a_missing_index_css(client, hello_project):
    """index.css is the one file every project is allowed not to have at
    all (see controller.py's own get_project_file docstring) — missing,
    this is 204 No Content, not a 404, so the "Edit project" view's
    always-mounted index.css editor can start the user off with an empty
    buffer instead of surfacing an error for a file that was never
    required to exist."""
    response = client.get(f"/api/projects/{hello_project}/files/index.css")
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.regression
def test_get_project_file_still_404s_for_a_missing_non_css_file(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/files/does-not-exist.txt")
    assert response.status_code == 404


@pytest.mark.regression
def test_image_upload_succeeds_with_matching_content_type(client, hello_project):
    response = client.put(
        f"/api/projects/{hello_project}/files/logo.png", content=PNG_MAGIC, headers={"Content-Type": "image/png"}
    )
    assert response.status_code == 200, response.text

    response = client.get(f"/api/projects/{hello_project}/files/logo.png")
    assert response.status_code == 200
    body = response.json()
    assert body["content_type"] == "image/png"
    # Raw bytes aren't JSON-serializable — the JSON route deliberately
    # omits them for a binary file (see get_project_file_content below).
    assert body["content"] is None


@pytest.mark.regression
def test_image_upload_rejects_mismatched_content_type(client, hello_project):
    response = client.put(
        f"/api/projects/{hello_project}/files/logo.png", content=PNG_MAGIC, headers={"Content-Type": "image/jpeg"}
    )
    assert response.status_code == 400


@pytest.mark.regression
def test_image_upload_rejects_missing_content_type(client, hello_project):
    response = client.put(f"/api/projects/{hello_project}/files/logo.png", content=PNG_MAGIC)
    assert response.status_code == 400


@pytest.mark.regression
def test_image_upload_rejects_oversized_file(client, hello_project):
    oversized = b"0" * (MAX_IMAGE_UPLOAD_BYTES + 1)
    response = client.put(
        f"/api/projects/{hello_project}/files/logo.png", content=oversized, headers={"Content-Type": "image/png"}
    )
    assert response.status_code == 400


@pytest.mark.regression
def test_index_css_save_rejects_a_missing_reference(client, hello_project):
    response = client.put(
        f"/api/projects/{hello_project}/files/index.css",
        content=b"body { background: url(missing-bg.png); }",
    )
    assert response.status_code == 400
    assert "missing-bg.png" in response.json()["error"]["message"]


@pytest.mark.regression
def test_index_css_save_accepts_an_existing_reference(client, hello_project):
    response = client.put(
        f"/api/projects/{hello_project}/files/bg.png", content=PNG_MAGIC, headers={"Content-Type": "image/png"}
    )
    assert response.status_code == 200, response.text

    response = client.put(
        f"/api/projects/{hello_project}/files/index.css",
        content=b"body { background: url('./bg.png'); }",
    )
    assert response.status_code == 200, response.text


@pytest.mark.regression
def test_index_css_save_ignores_absolute_url_references(client, hello_project):
    response = client.put(
        f"/api/projects/{hello_project}/files/index.css",
        content=b"body { background: url(https://example.com/bg.png); }",
    )
    assert response.status_code == 200, response.text


class TestGetProjectFileContent:
    def test_returns_raw_bytes_with_matching_media_type(self, client, hello_project):
        client.put(
            f"/api/projects/{hello_project}/files/logo.png", content=PNG_MAGIC, headers={"Content-Type": "image/png"}
        )

        response = client.get(f"/api/projects/{hello_project}/files/logo.png/content")

        assert response.status_code == 200
        assert response.content == PNG_MAGIC
        assert response.headers["content-type"] == "image/png"

    def test_404_for_an_unknown_file(self, client, hello_project):
        response = client.get(f"/api/projects/{hello_project}/files/does-not-exist.png/content")
        assert response.status_code == 404

    def test_etag_round_trips_to_a_304(self, client, hello_project):
        client.put(f"/api/projects/{hello_project}/files/index.css", content=b"body { color: red; }")

        first = client.get(f"/api/projects/{hello_project}/files/index.css/content")
        assert first.status_code == 200
        etag = first.headers["etag"]

        second = client.get(
            f"/api/projects/{hello_project}/files/index.css/content", headers={"If-None-Match": etag}
        )
        assert second.status_code == 304

    def test_without_session_id_reflects_the_current_draft(self, client, hello_project):
        client.put(f"/api/projects/{hello_project}/files/index.css", content=b"body { color: red; }")
        client.put(f"/api/projects/{hello_project}/files/index.css", content=b"body { color: blue; }")

        response = client.get(f"/api/projects/{hello_project}/files/index.css/content")

        assert response.content == b"body { color: blue; }"

    def test_a_live_session_stays_pinned_to_its_own_published_revision(self, client):
        # A two-state project with a real action (not hello_project's
        # single-state one): _finalize_project_update wipes every session
        # for the active project whenever its current_state can't be
        # determined at all (see test_controller_project_edit_preserves_
        # chat.py) — firing a real action is what establishes one reliably.
        client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.put("/api/projects/proj/files/index.css", content=b"body { color: red; }")
        client.post("/api/projects/proj/publish", json={})

        session_response = client.get("/api/chat/session")
        assert session_response.status_code == 200, session_response.text
        session_id = session_response.json()["id"]
        action_response = client.post("/api/action", json={"action_name": "go", "session_id": session_id})
        assert action_response.status_code == 200, action_response.text

        # A later edit + publish moves the draft/published revision ahead —
        # the already-created session must keep seeing revision 1.
        client.put("/api/projects/proj/files/index.css", content=b"body { color: blue; }")
        client.post("/api/projects/proj/publish", json={})

        pinned = client.get(f"/api/projects/proj/files/index.css/content?session_id={session_id}")
        current = client.get("/api/projects/proj/files/index.css/content")

        assert pinned.content == b"body { color: red; }"
        assert current.content == b"body { color: blue; }"

    def test_a_test_session_always_tracks_the_live_draft(self, client):
        client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.put("/api/projects/proj/files/index.css", content=b"body { color: red; }")
        client.post("/api/projects/proj/publish", json={})

        test_session_response = client.post("/api/projects/proj/test-sessions")
        assert test_session_response.status_code == 200, test_session_response.text
        test_session_id = test_session_response.json()["id"]
        action_response = client.post("/api/action", json={"action_name": "go", "session_id": test_session_id})
        assert action_response.status_code == 200, action_response.text

        # Edited *after* the Test session was already open — a 'test'
        # session must still see this, unlike a live/native one (see
        # test_a_live_session_stays_pinned_to_its_own_published_revision).
        client.put("/api/projects/proj/files/index.css", content=b"body { color: green; }")

        response = client.get(f"/api/projects/proj/files/index.css/content?session_id={test_session_id}")

        assert response.content == b"body { color: green; }"
