"""project.archive.media_migration — every Archive row still stored
under aspect/ that now belongs under media/, fixed in place, at every
revision, not only the current draft."""
from __future__ import annotations

import pytest

from db.models import Archive
from project.archive.media_migration import migrate_aspect_archives

pytestmark = pytest.mark.regression

PROJECT_ID = "migr_media_proj"
OTHER_PROJECT_ID = "migr_media_proj_2"


def _archive_names(project_id: str, revision: int) -> set[str]:
    return {
        row.archive_name for row in
        Archive.select().where((Archive.project == project_id) & (Archive.revision == revision))
    }


def test_an_image_under_aspect_moves_to_media_at_every_revision_it_was_stored_at(db):
    db.ensure_project(PROJECT_ID)
    db.write_archive_at_revision(PROJECT_ID, "aspect/icon.jpeg", 0, b"draft bytes", "image/jpeg")
    db.write_archive_at_revision(PROJECT_ID, "aspect/icon.jpeg", 1, b"published bytes", "image/jpeg")

    touched = migrate_aspect_archives(db)

    assert touched == {PROJECT_ID}
    assert _archive_names(PROJECT_ID, 0) == {"media/icon.jpeg"}
    assert _archive_names(PROJECT_ID, 1) == {"media/icon.jpeg"}
    assert db.get_archive(PROJECT_ID, "media/icon.jpeg", revision=0) == b"draft bytes"
    assert db.get_archive(PROJECT_ID, "media/icon.jpeg", revision=1) == b"published bytes"


def test_a_css_file_under_aspect_is_left_alone(db):
    """.css still canonicalizes under ASPECT_DIR (see automaton.file_types)
    — only an extension that now belongs under MEDIA_DIR ever moves."""
    db.ensure_project(PROJECT_ID)
    db.write_archive_at_revision(PROJECT_ID, "aspect/index.css", 0, b"body {}", "text/css")

    touched = migrate_aspect_archives(db)

    assert touched == set()
    assert _archive_names(PROJECT_ID, 0) == {"aspect/index.css"}


def test_a_target_that_already_exists_is_left_in_place_rather_than_overwritten(db):
    db.ensure_project(PROJECT_ID)
    db.write_archive_at_revision(PROJECT_ID, "media/logo.png", 0, b"the real one", "image/png")
    db.write_archive_at_revision(PROJECT_ID, "aspect/logo.png", 0, b"stale leftover", "image/png")

    touched = migrate_aspect_archives(db)

    assert touched == set()
    assert _archive_names(PROJECT_ID, 0) == {"media/logo.png", "aspect/logo.png"}
    assert db.get_archive(PROJECT_ID, "media/logo.png", revision=0) == b"the real one"


def test_every_project_is_covered_in_one_pass(db):
    db.ensure_project(PROJECT_ID)
    db.ensure_project(OTHER_PROJECT_ID)
    db.write_archive_at_revision(PROJECT_ID, "aspect/icon.png", 0, b"a", "image/png")
    db.write_archive_at_revision(OTHER_PROJECT_ID, "aspect/notify.mp3", 0, b"b", "audio/mpeg")

    touched = migrate_aspect_archives(db)

    assert touched == {PROJECT_ID, OTHER_PROJECT_ID}
    assert _archive_names(PROJECT_ID, 0) == {"media/icon.png"}
    assert _archive_names(OTHER_PROJECT_ID, 0) == {"media/notify.mp3"}


def test_nothing_under_aspect_is_a_clean_no_op(db):
    db.ensure_project(PROJECT_ID)
    db.write_archive_at_revision(PROJECT_ID, "media/icon.png", 0, b"a", "image/png")

    assert migrate_aspect_archives(db) == set()
    assert _archive_names(PROJECT_ID, 0) == {"media/icon.png"}


def test_running_it_twice_the_second_time_is_a_no_op(db):
    db.ensure_project(PROJECT_ID)
    db.write_archive_at_revision(PROJECT_ID, "aspect/icon.png", 0, b"a", "image/png")

    assert migrate_aspect_archives(db) == {PROJECT_ID}
    assert migrate_aspect_archives(db) == set()
