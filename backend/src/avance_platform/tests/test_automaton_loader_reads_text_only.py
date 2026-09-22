"""A build reads text files and the names of the others: a media file is
resolved by name (an attachment, a `media.<id>` reference) and its bytes
are never needed. The loader asks the database for exactly that, so a
project carrying megabytes of audio builds without pulling them into
memory on every health check and every session.
"""
from __future__ import annotations

import pytest

from project.archive.automaton_loader import AutomatonLoader

pytestmark = pytest.mark.contract

YML_WITH_MEDIA = """
attachments: [logo.png]
init-action:
  target: a
states:
  a:
    ui-label: A
    input-processor: ai
    contextual-prompt: hi
"""

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


def _seed(db, project_id: str) -> int:
    db.ensure_project(project_id)
    db.save_project_files(
        project_id,
        {"index.yml": YML_WITH_MEDIA.encode("utf-8"), "media/logo.png": PNG_BYTES},
        {"index.yml": "text/yaml", "media/logo.png": "image/png"},
    )
    return db.get_project_revision(project_id)


def test_get_archive_contents_returns_only_the_names_asked_for(db):
    revision = _seed(db, "media_proj")

    contents = db.get_archive_contents("media_proj", revision, ["index.yml"])

    assert set(contents) == {"index.yml"}
    assert contents["index.yml"] == YML_WITH_MEDIA.encode("utf-8")
    assert db.get_archive_contents("media_proj", revision, []) == {}


def test_a_media_attachment_resolves_by_name_through_the_loader(db):
    revision = _seed(db, "media_proj")

    automaton = AutomatonLoader(db).load_at_revision("media_proj", revision)

    assert automaton.general_attachments == ("media/logo.png",)
