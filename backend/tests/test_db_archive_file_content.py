"""Db-level tests for the Archive/File split: Archive keeps only the
metadata (project, name, revision) plus a content hash, and the bytes
themselves live once per distinct (content, content_type) in File.

File is deliberately an implementation detail — nothing above the Db
layer names it, so these tests reach for the model directly, unlike
every other db-level suite.
"""
from __future__ import annotations

import pytest

from db.models import Archive, File


@pytest.fixture(autouse=True)
def _user(db):
    db.get_or_create_user("test", "sub-user", "user", "user", None)


def _hashes(project="proj"):
    return [row.hash_id for row in Archive.select().where(Archive.project == project)]


@pytest.mark.regression
def test_put_is_idempotent_and_never_duplicates_the_same_content(db):
    first = File.put(b"same bytes", "text/yaml")
    second = File.put(b"same bytes", "text/yaml")

    assert first == second
    assert File.select().where(File.hash == first).count() == 1
    stored = File.get_by_id(first)
    assert stored.content == b"same bytes"
    assert stored.content_type == "text/yaml"
    assert stored.size == len(b"same bytes")


@pytest.mark.regression
def test_put_survives_a_concurrent_insert_of_the_same_content(db):
    key = File.hash_of(b"racing", "text/plain")
    File.insert(hash=key, content=b"racing", content_type="text/plain", size=6).execute()

    assert File.put(b"racing", "text/plain") == key
    assert File.select().where(File.hash == key).count() == 1


@pytest.mark.regression
def test_identical_content_across_projects_and_revisions_shares_one_file_row(db):
    db.save_project_files("proj", {"a.yml": b"shared"}, {"a.yml": "text/yaml"})
    db.save_project_files("other", {"b.yml": b"shared"}, {"b.yml": "text/yaml"})

    assert File.select().count() == 1
    assert db.get_archive("proj", "a.yml") == b"shared"
    assert db.get_archive("other", "b.yml") == b"shared"


@pytest.mark.regression
def test_publishing_and_forking_a_draft_reuses_the_published_revision_s_files(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")
    files_after_publish = File.select().count()

    db.save_project_files("proj", {"other.yml": b"v0"}, {"other.yml": "text/yaml"})

    published = db.get_project_published_revision("proj")
    draft = db.get_project_revision("proj")
    assert draft != published
    assert db.get_archive("proj", "index.yml", revision=draft) == b"v0"
    assert File.select().count() == files_after_publish


@pytest.mark.regression
def test_renaming_a_file_moves_only_archive_metadata(db):
    db.save_project_files("proj", {"old.yml": b"payload"}, {"old.yml": "text/yaml"})
    hash_before = _hashes()

    db.rename_archive("proj", "old.yml", "new.yml")

    assert db.list_archives("proj") == ["new.yml"]
    assert db.get_archive("proj", "new.yml") == b"payload"
    assert _hashes() == hash_before
    assert File.select().count() == 1


@pytest.mark.regression
def test_same_bytes_under_different_content_types_stay_separate_files(db):
    db.save_project_files(
        "proj", {"index.yml": b"", "index.css": b""},
        {"index.yml": "text/yaml", "index.css": "text/css"},
    )

    assert File.select().count() == 2
    assert db.get_archive_content_type("proj", "index.yml") == "text/yaml"
    assert db.get_archive_content_type("proj", "index.css") == "text/css"


@pytest.mark.regression
def test_a_file_is_dropped_once_no_archive_row_references_it(db):
    db.save_project_files("proj", {"a.yml": b"only here"}, {"a.yml": "text/yaml"})
    assert File.select().count() == 1

    db.delete_archive("proj", "a.yml")

    assert File.select().count() == 0


@pytest.mark.regression
def test_a_file_survives_while_another_archive_row_still_references_it(db):
    db.save_project_files(
        "proj", {"a.yml": b"shared", "b.yml": b"shared"},
        {"a.yml": "text/yaml", "b.yml": "text/yaml"},
    )
    assert File.select().count() == 1

    db.delete_archive("proj", "a.yml")

    assert File.select().count() == 1
    assert db.get_archive("proj", "b.yml") == b"shared"


@pytest.mark.regression
def test_overwriting_a_file_drops_the_content_nobody_references_any_more(db):
    db.save_project_files("proj", {"a.yml": b"v0"}, {"a.yml": "text/yaml"})
    db.save_project_files("proj", {"a.yml": b"v1"}, {"a.yml": "text/yaml"})

    assert [f.content for f in File.select()] == [b"v1"]


@pytest.mark.regression
def test_deleting_a_whole_project_drops_its_files(db):
    db.save_project_files("proj", {"a.yml": b"gone soon"}, {"a.yml": "text/yaml"})

    db.delete_archives("proj")

    assert File.select().count() == 0
