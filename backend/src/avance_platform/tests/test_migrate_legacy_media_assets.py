"""ProjectEditor.migrate_legacy_media_assets — the one-time fix-up for a
project saved before image/audio extensions canonicalized into `media/`
(see automaton.file_types), when they still lived under `aspect/`.
`POST .../media/migrate` moves any surviving `aspect/<name>` archive to
`media/<name>`, keeping its content, and is what "Edit project" calls on
open (alongside index-yml/modernize) — see EditProjectView.vue's own
onMounted.

A legacy `aspect/<name>` row can no longer be created through the normal
upload endpoint (canonicalize_name rejects a target that doesn't match
today's rules), so these tests seed one directly through Db, the way an
already-stored row from before this change actually looks."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _seed_legacy_aspect_image(app_db, project_id: str, name: str = "aspect/logo.png", content: bytes = PNG_MAGIC) -> None:
    app_db.save_project_files(project_id, {name: content}, {name: "image/png"})


def _migrate(client, project_id: str):
    return client.post(f"/api/skills/platform/projects/{project_id}/media/migrate", json={})


def test_a_legacy_aspect_image_moves_to_media_keeping_its_content(client, hello_project, app_db):
    _seed_legacy_aspect_image(app_db, hello_project)

    response = _migrate(client, hello_project)
    assert response.status_code == 200, response.text
    assert response.json()["moved"] == ["media/logo.png"]

    files = client.get(f"/api/skills/platform/projects/{hello_project}/files").json()["files"]
    assert "media/logo.png" in files
    assert "aspect/logo.png" not in files

    content = client.get(f"/api/core/projects/{hello_project}/files/media/logo.png/content")
    assert content.status_code == 200
    assert content.content == PNG_MAGIC


def test_index_css_keeps_resolving_the_same_url_reference_after_migration(client, hello_project, app_db):
    """A url(...) reference is resolved by basename only (CssValidator) —
    migrating never needs to touch index.css's own text."""
    _seed_legacy_aspect_image(app_db, hello_project)
    assert _migrate(client, hello_project).status_code == 200

    saved = client.put(
        f"/api/skills/platform/projects/{hello_project}/files/index.css",
        content=b"body { background: url('logo.png'); }",
    )
    assert saved.status_code == 200, saved.text


def test_is_idempotent_and_a_no_op_with_nothing_left_under_aspect(client, hello_project, app_db):
    _seed_legacy_aspect_image(app_db, hello_project)
    assert _migrate(client, hello_project).json()["moved"] == ["media/logo.png"]

    second = _migrate(client, hello_project)
    assert second.status_code == 200, second.text
    assert second.json()["moved"] == []


def test_leaves_a_legacy_file_in_place_if_media_already_has_one_of_the_same_name(client, hello_project, app_db):
    _seed_legacy_aspect_image(app_db, hello_project)
    client.put(
        f"/api/skills/platform/projects/{hello_project}/files/media/logo.png",
        content=b"\x89PNG already here" + b"0" * 16,
        headers={"Content-Type": "image/png"},
    )

    response = _migrate(client, hello_project)
    assert response.status_code == 200, response.text
    assert response.json()["moved"] == []

    files = client.get(f"/api/skills/platform/projects/{hello_project}/files").json()["files"]
    assert "aspect/logo.png" in files
    assert "media/logo.png" in files


def test_an_unknown_project_404s(client):
    assert _migrate(client, "does-not-exist").status_code == 404
