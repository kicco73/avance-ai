"""ProjectEditor.rename_project_file: renames a file's Archive row in
place, auto-rewrites any literal reference to its old basename in
index.yml/index.css, and reversibly via the file's own undo/redo stack
(a rename-marker EditHistory entry, see db/history.py).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

_INDEX_YML_WITH_ATTACHMENT = """\
avance-version: "1.7.0"

init-action:
  target: Hello

states:
  Hello:
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
    attachments: [notes.md]
project:
  ui-label: Hello, world!
  id: hello_world
"""


def _upload_attachment(client, project_id, name="behaviour/notes.md", content="hello notes"):
    response = client.put(f"/api/projects/{project_id}/files/{name}", content=content)
    assert response.status_code == 200, response.text
    return response.json()


def _rename(client, project_id, name, new_name):
    return client.post(f"/api/projects/{project_id}/files/{name}/rename", json={"new_name": new_name})


def _files(client, project_id) -> list[str]:
    return client.get(f"/api/projects/{project_id}/files").json()["files"]


def test_renaming_an_attachment_moves_its_row_and_auto_rewrites_every_index_yml_reference(client, hello_project):
    _upload_attachment(client, hello_project)
    assert client.put(f"/api/projects/{hello_project}/files/index.yml", content=_INDEX_YML_WITH_ATTACHMENT).status_code == 200

    response = _rename(client, hello_project, "behaviour/notes.md", "memo.md")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["old_name"] == "behaviour/notes.md"
    assert payload["content"] == "hello notes"

    files = _files(client, hello_project)
    assert "behaviour/memo.md" in files
    assert "behaviour/notes.md" not in files
    assert client.get(f"/api/projects/{hello_project}/files/behaviour/notes.md").status_code == 404

    index_yml = client.get(f"/api/projects/{hello_project}/files/index.yml").json()["content"]
    assert "memo.md" in index_yml
    assert "notes.md" not in index_yml


def test_a_rename_is_refused_for_index_yml_a_taken_name_a_different_category_or_a_name_carrying_a_path(client, hello_project):
    """Only a plain basename is ever accepted — the folder (aspect/
    behaviour) a rename keeps fixed can't be smuggled in via new_name."""
    _upload_attachment(client, hello_project)
    _upload_attachment(client, hello_project, name="behaviour/b.md", content="b")

    assert _rename(client, hello_project, "index.yml", "other.yml").status_code == 400
    assert _rename(client, hello_project, "behaviour/notes.md", "b.md").status_code == 400
    assert _rename(client, hello_project, "behaviour/notes.md", "notes.png").status_code == 400
    assert _rename(client, hello_project, "behaviour/notes.md", "aspect/notes.md").status_code == 400

    assert "behaviour/notes.md" in _files(client, hello_project)


def test_undo_reverses_a_rename_and_redo_reapplies_it_leaving_the_old_names_own_history_intact(client, hello_project):
    """The file's undo stack from *before* it was ever renamed must still
    be there once undo moves it back to its old name — nothing migrates
    rows, the old name's stack was simply left untouched."""
    _upload_attachment(client, hello_project, content="v1")
    client.put(f"/api/projects/{hello_project}/files/behaviour/notes.md", content="v2")
    _rename(client, hello_project, "behaviour/notes.md", "memo.md")

    undo_response = client.post(f"/api/projects/{hello_project}/files/behaviour/memo.md/undo", content="")
    assert undo_response.status_code == 200, undo_response.text
    assert undo_response.json()["renamed_to"] == "behaviour/notes.md"
    assert undo_response.json()["content"] == "v2"
    files = _files(client, hello_project)
    assert "behaviour/notes.md" in files
    assert "behaviour/memo.md" not in files

    # The pre-rename stack is still there under the old name.
    assert client.get(f"/api/projects/{hello_project}/files/behaviour/notes.md").json()["can_undo"] is True
    content_undo = client.post(f"/api/projects/{hello_project}/files/behaviour/notes.md/undo", content="v2")
    assert content_undo.status_code == 200, content_undo.text
    assert content_undo.json()["content"] == "v1"

    redo_response = client.post(f"/api/projects/{hello_project}/files/behaviour/notes.md/redo", content="v1")
    assert redo_response.status_code == 200, redo_response.text
