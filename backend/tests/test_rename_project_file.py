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


class TestRenameProjectFile:
    def test_renames_a_plain_attachment(self, client, hello_project):
        _upload_attachment(client, hello_project)

        response = client.post(
            f"/api/projects/{hello_project}/files/behaviour/notes.md/rename", json={"new_name": "memo.md"}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["old_name"] == "behaviour/notes.md"
        assert payload["content"] == "hello notes"

        files = client.get(f"/api/projects/{hello_project}/files").json()["files"]
        assert "behaviour/memo.md" in files
        assert "behaviour/notes.md" not in files

        assert client.get(f"/api/projects/{hello_project}/files/behaviour/notes.md").status_code == 404

    def test_auto_rewrites_index_yml_references(self, client, hello_project):
        _upload_attachment(client, hello_project)
        response = client.put(f"/api/projects/{hello_project}/files/index.yml", content=_INDEX_YML_WITH_ATTACHMENT)
        assert response.status_code == 200, response.text

        response = client.post(
            f"/api/projects/{hello_project}/files/behaviour/notes.md/rename", json={"new_name": "memo.md"}
        )
        assert response.status_code == 200, response.text

        index_yml = client.get(f"/api/projects/{hello_project}/files/index.yml").json()["content"]
        assert "memo.md" in index_yml
        assert "notes.md" not in index_yml

    def test_rejects_a_name_that_already_exists(self, client, hello_project):
        _upload_attachment(client, hello_project, name="behaviour/a.md", content="a")
        _upload_attachment(client, hello_project, name="behaviour/b.md", content="b")

        response = client.post(f"/api/projects/{hello_project}/files/behaviour/a.md/rename", json={"new_name": "b.md"})
        assert response.status_code == 400

    def test_rejects_renaming_index_yml(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/files/index.yml/rename", json={"new_name": "other.yml"})
        assert response.status_code == 400

    def test_rejects_changing_file_category(self, client, hello_project):
        _upload_attachment(client, hello_project)
        response = client.post(
            f"/api/projects/{hello_project}/files/behaviour/notes.md/rename", json={"new_name": "notes.png"}
        )
        assert response.status_code == 400

    def test_rejects_a_new_name_containing_a_path(self, client, hello_project):
        """Only a plain basename is ever accepted — the folder (aspect/
        behaviour) a rename keeps fixed can't be smuggled in via new_name."""
        _upload_attachment(client, hello_project)
        response = client.post(
            f"/api/projects/{hello_project}/files/behaviour/notes.md/rename",
            json={"new_name": "aspect/notes.md"},
        )
        assert response.status_code == 400

        files = client.get(f"/api/projects/{hello_project}/files").json()["files"]
        assert "behaviour/notes.md" in files


class TestRenameUndoRedo:
    def test_undo_reverses_the_rename_and_redo_reapplies_it(self, client, hello_project):
        _upload_attachment(client, hello_project)
        client.post(f"/api/projects/{hello_project}/files/behaviour/notes.md/rename", json={"new_name": "memo.md"})

        undo_response = client.post(f"/api/projects/{hello_project}/files/behaviour/memo.md/undo", content="")
        assert undo_response.status_code == 200, undo_response.text
        undo_payload = undo_response.json()
        assert undo_payload["renamed_to"] == "behaviour/notes.md"
        assert undo_payload["content"] == "hello notes"

        files = client.get(f"/api/projects/{hello_project}/files").json()["files"]
        assert "behaviour/notes.md" in files
        assert "behaviour/memo.md" not in files

        redo_response = client.post(f"/api/projects/{hello_project}/files/behaviour/notes.md/redo", content="")
        assert redo_response.status_code == 200, redo_response.text
        redo_payload = redo_response.json()
        assert redo_payload["renamed_to"] == "behaviour/memo.md"

        files = client.get(f"/api/projects/{hello_project}/files").json()["files"]
        assert "behaviour/memo.md" in files
        assert "behaviour/notes.md" not in files

    def test_old_names_own_history_survives_a_rename_undone(self, client, hello_project):
        """The file's undo stack from *before* it was ever renamed must
        still be there once undo moves it back to its old name — nothing
        migrates rows, the old name's stack was simply left untouched."""
        _upload_attachment(client, hello_project, content="v1")
        client.put(f"/api/projects/{hello_project}/files/behaviour/notes.md", content="v2")

        client.post(f"/api/projects/{hello_project}/files/behaviour/notes.md/rename", json={"new_name": "memo.md"})
        client.post(f"/api/projects/{hello_project}/files/behaviour/memo.md/undo", content="")

        info = client.get(f"/api/projects/{hello_project}/files/behaviour/notes.md").json()
        assert info["can_undo"] is True

        undo_response = client.post(f"/api/projects/{hello_project}/files/behaviour/notes.md/undo", content="v2")
        assert undo_response.status_code == 200, undo_response.text
        assert undo_response.json()["content"] == "v1"
