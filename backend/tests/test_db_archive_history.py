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


@pytest.mark.contract
def test_get_archive_returns_none_for_an_unknown_file(db):
    assert db.get_archive("proj", "missing.yml") is None


@pytest.mark.regression
def test_save_project_files_upserts_current_content(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    assert db.get_archive("proj", "index.yml") == b"v0"

    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})
    assert db.get_archive("proj", "index.yml") == b"v1"


@pytest.mark.regression
def test_save_project_files_writes_every_entry_given(db):
    db.save_project_files(
        "proj", {"index.yml": b"yml", "notes.txt": b"notes"},
        {"index.yml": "text/yaml", "notes.txt": "text/plain"},
    )
    assert db.get_archives("proj") == {"index.yml": b"yml", "notes.txt": b"notes"}


@pytest.mark.regression
def test_save_project_files_never_creates_a_second_row_for_the_same_file(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})
    db.save_project_files("proj", {"index.yml": b"v2"}, {"index.yml": "text/yaml"})

    assert db.list_archives("proj") == ["index.yml"]


@pytest.mark.regression
def test_save_project_files_does_not_push_undo_history(db):
    """The bulk path (project upload/replace) never touches History —
    only save_project_file (the single-file editor Save) does."""
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})

    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_save_project_file_first_save_has_nothing_to_undo(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")

    assert db.get_archive("proj", "index.yml") == b"v0"
    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_save_project_file_pushes_previous_content_to_undo(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")

    assert db.get_archive("proj", "index.yml") == b"v1"
    assert db.has_undo("user", "proj", "index.yml") is True


@pytest.mark.regression
def test_save_project_file_clears_redo_history(db):
    """A fresh edit invalidates whatever redo could have replayed."""
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")
    db.undo_project_file("user", "proj", "index.yml", b"v1")
    assert db.has_redo("user", "proj", "index.yml") is True

    db.save_project_file("user", "proj", "index.yml", b"v2", "text/yaml")

    assert db.has_redo("user", "proj", "index.yml") is False


@pytest.mark.contract
def test_undo_with_nothing_to_undo_is_a_noop(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")

    assert db.undo_project_file("user", "proj", "index.yml", b"v0") is None
    assert db.get_archive("proj", "index.yml") == b"v0"


@pytest.mark.regression
def test_undo_returns_previous_content_and_enables_redo(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")

    restored = db.undo_project_file("user", "proj", "index.yml", b"v1")

    assert restored.content == b"v0"
    assert db.has_undo("user", "proj", "index.yml") is False
    assert db.has_redo("user", "proj", "index.yml") is True


@pytest.mark.regression
def test_undo_never_touches_archive(db):
    """Undo is a pure preview: Archive keeps whatever the last real save
    left it at, regardless of what undo/redo has previewed since."""
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")

    db.undo_project_file("user", "proj", "index.yml", b"v1")

    assert db.get_archive("proj", "index.yml") == b"v1"
    assert db.list_archives("proj") == ["index.yml"]


@pytest.mark.contract
def test_redo_with_nothing_to_redo_is_a_noop(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")

    assert db.redo_project_file("user", "proj", "index.yml", b"v0") is None
    assert db.get_archive("proj", "index.yml") == b"v0"


@pytest.mark.regression
def test_redo_replays_undone_content_and_enables_undo_again(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")
    db.undo_project_file("user", "proj", "index.yml", b"v1")  # now previewing "v0"

    replayed = db.redo_project_file("user", "proj", "index.yml", b"v0")  # editor currently shows "v0"

    assert replayed.content == b"v1"
    assert db.has_undo("user", "proj", "index.yml") is True
    assert db.has_redo("user", "proj", "index.yml") is False
    # Neither undo nor redo ever touched Archive — still whatever the
    # real save left it at.
    assert db.get_archive("proj", "index.yml") == b"v1"


@pytest.mark.regression
def test_multiple_undo_then_multiple_redo_walk_the_full_trail(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v2", "text/yaml")

    # Each call passes whatever the editor is currently previewing —
    # exactly what the previous call just returned.
    assert db.undo_project_file("user", "proj", "index.yml", b"v2").content == b"v1"
    assert db.undo_project_file("user", "proj", "index.yml", b"v1").content == b"v0"
    assert db.undo_project_file("user", "proj", "index.yml", b"v0") is None  # nothing before v0

    assert db.redo_project_file("user", "proj", "index.yml", b"v0").content == b"v1"
    assert db.redo_project_file("user", "proj", "index.yml", b"v1").content == b"v2"
    assert db.redo_project_file("user", "proj", "index.yml", b"v2") is None  # nothing past v2
    # Archive was never touched by any of the above.
    assert db.get_archive("proj", "index.yml") == b"v2"


@pytest.mark.regression
def test_history_is_scoped_per_user(db):
    db.save_project_file("alice", "proj", "index.yml", b"alice-v0", "text/yaml")
    db.save_project_file("alice", "proj", "index.yml", b"alice-v1", "text/yaml")

    # bob never saved index.yml himself — his own undo stack is empty
    # even though the file has history for alice.
    assert db.has_undo("bob", "proj", "index.yml") is False
    assert db.undo_project_file("bob", "proj", "index.yml", b"whatever bob has open") is None
    # alice's own trail is untouched by bob's no-op.
    assert db.has_undo("alice", "proj", "index.yml") is True


@pytest.mark.regression
def test_clear_history_removes_every_files_history_for_the_project(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")
    db.save_project_file("user", "proj", "notes.txt", b"n0", "text/plain")
    db.save_project_file("user", "proj", "notes.txt", b"n1", "text/plain")

    db.clear_history("user", "proj")

    assert db.has_undo("user", "proj", "index.yml") is False
    assert db.has_undo("user", "proj", "notes.txt") is False
    # Clearing history never touches current content.
    assert db.get_archive("proj", "index.yml") == b"v1"
    assert db.get_archive("proj", "notes.txt") == b"n1"


@pytest.mark.regression
def test_clear_history_is_scoped_to_its_own_project(db):
    db.save_project_file("user", "proj-a", "index.yml", b"a-v0", "text/yaml")
    db.save_project_file("user", "proj-a", "index.yml", b"a-v1", "text/yaml")
    db.save_project_file("user", "proj-b", "index.yml", b"b-v0", "text/yaml")
    db.save_project_file("user", "proj-b", "index.yml", b"b-v1", "text/yaml")

    db.clear_history("user", "proj-a")

    assert db.has_undo("user", "proj-a", "index.yml") is False
    assert db.has_undo("user", "proj-b", "index.yml") is True


@pytest.mark.regression
def test_clear_history_is_scoped_to_its_own_user(db):
    db.save_project_file("alice", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("alice", "proj", "index.yml", b"v1", "text/yaml")
    db.save_project_file("bob", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("bob", "proj", "index.yml", b"v1", "text/yaml")

    db.clear_history("alice", "proj")

    assert db.has_undo("alice", "proj", "index.yml") is False
    assert db.has_undo("bob", "proj", "index.yml") is True


@pytest.mark.regression
def test_get_archives_returns_only_current_content(db):
    db.save_project_files(
        "proj", {"index.yml": b"v0", "notes.txt": b"n0"}, {"index.yml": "text/yaml", "notes.txt": "text/plain"}
    )
    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})

    assert db.get_archives("proj") == {"index.yml": b"v1", "notes.txt": b"n0"}


@pytest.mark.regression
def test_get_archives_is_scoped_to_its_own_project(db):
    db.save_project_files("proj-a", {"index.yml": b"a"}, {"index.yml": "text/yaml"})
    db.save_project_files("proj-b", {"index.yml": b"b"}, {"index.yml": "text/yaml"})

    assert db.get_archives("proj-a") == {"index.yml": b"a"}


@pytest.mark.regression
def test_delete_archive_removes_it_and_its_history(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")

    db.delete_archive("proj", "index.yml")

    assert db.get_archive("proj", "index.yml") is None
    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_delete_archives_removes_every_file_and_all_their_history(db):
    db.save_project_file("user", "proj", "index.yml", b"v0", "text/yaml")
    db.save_project_file("user", "proj", "index.yml", b"v1", "text/yaml")
    db.save_project_file("user", "proj", "notes.txt", b"n0", "text/plain")

    db.delete_archives("proj")

    assert db.get_archives("proj") == {}
    assert db.list_archives("proj") == []
    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_archive_content_type_is_persisted_and_updated_on_resave(db):
    db.save_project_file("user", "proj", "logo.png", b"\x89PNG", "image/png")
    assert db.get_archive_content_type("proj", "logo.png") == "image/png"

    db.save_project_file("user", "proj", "logo.png", b"\x89PNG-2", "image/png")
    assert db.get_archive_content_type("proj", "logo.png") == "image/png"


@pytest.mark.regression
def test_delete_unused_archive_revisions_removes_a_superseded_unpublished_draft(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")  # revision 0 published
    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})  # forks to revision 1
    db.publish_project("proj")  # revision 1 published — revision 0 is now unused
    db.save_project_files("proj", {"index.yml": b"v2"}, {"index.yml": "text/yaml"})  # forks to revision 2 (draft)

    deleted = db.delete_unused_archive_revisions()

    assert deleted == 1
    assert db.list_archives("proj", revision=0) == []
    assert db.list_archives("proj", revision=1) == ["index.yml"]  # published, kept
    assert db.list_archives("proj", revision=2) == ["index.yml"]  # current draft, kept


@pytest.mark.regression
def test_delete_unused_archive_revisions_keeps_a_revision_pinned_by_a_session(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")  # revision 0 published
    db.create_chat_session(username="user", project_id="proj", revision=0, start_state="a")
    db.save_project_files("proj", {"index.yml": b"v1"}, {"index.yml": "text/yaml"})  # forks to revision 1
    db.publish_project("proj")  # revision 1 published — revision 0 is unpublished now, but still pinned by a session

    deleted = db.delete_unused_archive_revisions()

    assert deleted == 0
    assert db.list_archives("proj", revision=0) == ["index.yml"]


@pytest.mark.regression
def test_delete_unused_archive_revisions_covers_every_project_at_once(db):
    db.save_project_files("proj-a", {"index.yml": b"a-v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj-a")
    db.save_project_files("proj-a", {"index.yml": b"a-v1"}, {"index.yml": "text/yaml"})
    db.publish_project("proj-a")  # proj-a's revision 0 is now unused

    db.save_project_files("proj-b", {"index.yml": b"b-v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj-b")  # proj-b has a single revision — nothing to clean

    deleted = db.delete_unused_archive_revisions()

    assert deleted == 1
    assert db.list_archives("proj-a", revision=0) == []
    assert db.list_archives("proj-b", revision=0) == ["index.yml"]


@pytest.mark.contract
def test_delete_unused_archive_revisions_is_a_noop_when_nothing_is_unused(db):
    db.save_project_files("proj", {"index.yml": b"v0"}, {"index.yml": "text/yaml"})
    db.publish_project("proj")

    assert db.delete_unused_archive_revisions() == 0
