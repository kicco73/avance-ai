"""Db-level tests for Archive (a file's current content, one row per
project+file) and History (per-(user, project, file) undo/redo trail).
Content is bytes-native (BlobField), with a persisted content_type.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _users(db):
    """EditHistory.user_id is a real FK onto User now (see models.py) — every
    identifier this file's tests use as a "user" needs a matching row."""
    for username in ("user", "alice", "bob"):
        db.get_or_create_user("test", f"sub-{username}", username, username, None)


def _save(db, content: bytes, user="user", project="proj", name="index.yml", content_type="text/yaml"):
    db.save_project_file(user, project, name, content, content_type)


@pytest.mark.regression
def test_save_project_files_upserts_every_entry_in_place_per_project_and_never_pushes_undo_history(db):
    """The bulk path (project upload/replace) never touches History —
    only save_project_file (the single-file editor Save) does."""
    assert db.get_archive("proj", "missing.yml") is None

    db.save_project_files("proj", {"index.yml": b"v0", "notes.txt": b"n0"}, {"index.yml": "text/yaml", "notes.txt": "text/plain"})
    assert db.get_archives("proj") == {"index.yml": b"v0", "notes.txt": b"n0"}

    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})
    db.save_project_files("proj", {"index.yml": b"v2"}, {"index.yml": "text/yaml"})
    assert db.get_archive("proj", "index.yml") == b"v2"
    assert db.get_archives("proj") == {"index.yml": b"v2", "notes.txt": b"n0"}
    assert db.list_archives("proj") == ["index.yml", "notes.txt"]
    assert db.has_undo("user", "proj", "index.yml") is False

    db.save_project_files("proj-b", {"index.yml": b"b"}, {"index.yml": "text/yaml"})
    assert db.get_archives("proj-b") == {"index.yml": b"b"}


@pytest.mark.regression
def test_save_project_file_pushes_the_previous_content_to_undo_and_a_fresh_edit_clears_redo(db):
    _save(db, b"v0")
    assert db.get_archive("proj", "index.yml") == b"v0"
    assert db.has_undo("user", "proj", "index.yml") is False

    _save(db, b"v1")
    assert db.get_archive("proj", "index.yml") == b"v1"
    assert db.has_undo("user", "proj", "index.yml") is True

    db.undo_project_file("user", "proj", "index.yml", b"v1")
    assert db.has_redo("user", "proj", "index.yml") is True
    _save(db, b"v2")
    assert db.has_redo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_undo_and_redo_are_pure_previews_walking_the_full_trail_without_ever_touching_archive(db):
    """Archive keeps whatever the last real save left it at, regardless of
    what undo/redo has previewed since. Each call passes whatever the
    editor is currently previewing — exactly what the previous call just
    returned."""
    _save(db, b"v0")
    assert db.undo_project_file("user", "proj", "index.yml", b"v0") is None
    assert db.redo_project_file("user", "proj", "index.yml", b"v0") is None
    assert db.get_archive("proj", "index.yml") == b"v0"

    _save(db, b"v1")
    _save(db, b"v2")

    assert db.undo_project_file("user", "proj", "index.yml", b"v2").content == b"v1"
    assert db.has_undo("user", "proj", "index.yml") is True
    assert db.has_redo("user", "proj", "index.yml") is True
    assert db.undo_project_file("user", "proj", "index.yml", b"v1").content == b"v0"
    assert db.has_undo("user", "proj", "index.yml") is False
    assert db.undo_project_file("user", "proj", "index.yml", b"v0") is None

    assert db.redo_project_file("user", "proj", "index.yml", b"v0").content == b"v1"
    assert db.redo_project_file("user", "proj", "index.yml", b"v1").content == b"v2"
    assert db.has_undo("user", "proj", "index.yml") is True
    assert db.has_redo("user", "proj", "index.yml") is False
    assert db.redo_project_file("user", "proj", "index.yml", b"v2") is None

    assert db.get_archive("proj", "index.yml") == b"v2"
    assert db.list_archives("proj") == ["index.yml"]


@pytest.mark.regression
def test_history_is_scoped_per_user_and_clear_history_drops_only_that_users_trail_for_that_project(db):
    _save(db, b"alice-v0", user="alice")
    _save(db, b"alice-v1", user="alice")
    _save(db, b"n0", user="alice", name="notes.txt", content_type="text/plain")
    _save(db, b"n1", user="alice", name="notes.txt", content_type="text/plain")
    _save(db, b"bob-v0", user="bob")
    _save(db, b"bob-v1", user="bob")
    _save(db, b"b-v0", user="alice", project="proj-b")
    _save(db, b"b-v1", user="alice", project="proj-b")

    # bob's own undo stack on notes.txt is empty even though the file has
    # history for alice, and his no-op leaves alice's trail untouched.
    assert db.has_undo("bob", "proj", "notes.txt") is False
    assert db.undo_project_file("bob", "proj", "notes.txt", b"whatever bob has open") is None
    assert db.has_undo("alice", "proj", "notes.txt") is True

    db.clear_history("alice", "proj")

    assert db.has_undo("alice", "proj", "index.yml") is False
    assert db.has_undo("alice", "proj", "notes.txt") is False
    assert db.has_undo("bob", "proj", "index.yml") is True
    assert db.has_undo("alice", "proj-b", "index.yml") is True
    # Clearing history never touches current content.
    assert db.get_archive("proj", "index.yml") == b"bob-v1"
    assert db.get_archive("proj", "notes.txt") == b"n1"


@pytest.mark.regression
def test_deleting_an_archive_or_every_archive_drops_their_history_too_and_content_type_survives_resaves(db):
    _save(db, b"\x89PNG", name="logo.png", content_type="image/png")
    _save(db, b"\x89PNG-2", name="logo.png", content_type="image/png")
    assert db.get_archive_content_type("proj", "logo.png") == "image/png"

    _save(db, b"v0")
    _save(db, b"v1")
    db.delete_archive("proj", "index.yml")
    assert db.get_archive("proj", "index.yml") is None
    assert db.has_undo("user", "proj", "index.yml") is False
    assert db.list_archives("proj") == ["logo.png"]

    _save(db, b"n0", name="notes.txt", content_type="text/plain")
    db.delete_archives("proj")
    assert db.get_archives("proj") == {}
    assert db.list_archives("proj") == []
    assert db.has_undo("user", "proj", "logo.png") is False


@pytest.mark.regression
def test_delete_unused_archive_revisions_removes_superseded_unpublished_drafts_across_every_project(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")
    assert db.delete_unused_archive_revisions() == 0

    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")  # revision 1 published — revision 0 is now unused
    db.save_project_files("proj", {"index.yml": b"v2"}, {"index.yml": "text/yaml"})  # revision 2 (draft)

    db.save_project_files("proj-a", {"index.yml": b"a-v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj-a")
    db.save_project_files("proj-a", {"index.yml": b"a-v1"}, {"index.yml": "text/yaml"})
    db.publish_project("proj-a")  # proj-a's revision 0 is now unused
    db.save_project_files("proj-b", {"index.yml": b"b-v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj-b")  # a single revision — nothing to clean

    assert db.delete_unused_archive_revisions() == 2
    assert db.list_archives("proj", revision=0) == []
    assert db.list_archives("proj", revision=1) == ["index.yml"]
    assert db.list_archives("proj", revision=2) == ["index.yml"]
    assert db.list_archives("proj-a", revision=0) == []
    assert db.list_archives("proj-b", revision=0) == ["index.yml"]


@pytest.mark.regression
def test_delete_unused_archive_revisions_keeps_a_revision_pinned_by_a_session(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")
    db.create_chat_session(username="user", project_id="proj", revision=0, start_state="a")
    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")  # revision 0 is unpublished now, but still pinned by a session

    assert db.delete_unused_archive_revisions() == 0
    assert db.list_archives("proj", revision=0) == ["index.yml"]
