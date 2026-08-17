"""Db-level tests for the Archive/History redesign: Archive holds only a
file's current content (one row per project+file, no version history),
and per-(user, project, file) undo/redo lives entirely in History — see
backend/src/db.py's own Archive/History docstrings.
"""
from __future__ import annotations

import pytest


@pytest.mark.contract
def test_get_archive_returns_none_for_an_unknown_file(db):
    assert db.get_archive("proj", "missing.yml") is None


@pytest.mark.regression
def test_save_project_files_upserts_current_content(db):
    db.save_project_files("proj", {"index.yml": "v0"})
    assert db.get_archive("proj", "index.yml") == "v0"

    db.save_project_files("proj", {"index.yml": "v1"})
    assert db.get_archive("proj", "index.yml") == "v1"


@pytest.mark.regression
def test_save_project_files_writes_every_entry_given(db):
    db.save_project_files("proj", {"index.yml": "yml", "notes.txt": "notes"})
    assert db.get_archives("proj") == {"index.yml": "yml", "notes.txt": "notes"}


@pytest.mark.regression
def test_save_project_files_never_creates_a_second_row_for_the_same_file(db):
    db.save_project_files("proj", {"index.yml": "v0"})
    db.save_project_files("proj", {"index.yml": "v1"})
    db.save_project_files("proj", {"index.yml": "v2"})

    assert db.list_archives("proj") == ["index.yml"]


@pytest.mark.regression
def test_save_project_files_does_not_push_undo_history(db):
    """The bulk path (project upload/replace) never touches History —
    only save_project_file (the single-file editor Save) does."""
    db.save_project_files("proj", {"index.yml": "v0"})
    db.save_project_files("proj", {"index.yml": "v1"})

    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_save_project_file_first_save_has_nothing_to_undo(db):
    db.save_project_file("user", "proj", "index.yml", "v0")

    assert db.get_archive("proj", "index.yml") == "v0"
    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_save_project_file_pushes_previous_content_to_undo(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")

    assert db.get_archive("proj", "index.yml") == "v1"
    assert db.has_undo("user", "proj", "index.yml") is True


@pytest.mark.regression
def test_save_project_file_clears_redo_history(db):
    """A fresh edit invalidates whatever redo could have replayed."""
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")
    db.undo_project_file("user", "proj", "index.yml", "v1")
    assert db.has_redo("user", "proj", "index.yml") is True

    db.save_project_file("user", "proj", "index.yml", "v2")

    assert db.has_redo("user", "proj", "index.yml") is False


@pytest.mark.contract
def test_undo_with_nothing_to_undo_is_a_noop(db):
    db.save_project_file("user", "proj", "index.yml", "v0")

    assert db.undo_project_file("user", "proj", "index.yml", "v0") is None
    assert db.get_archive("proj", "index.yml") == "v0"


@pytest.mark.regression
def test_undo_returns_previous_content_and_enables_redo(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")

    restored = db.undo_project_file("user", "proj", "index.yml", "v1")

    assert restored == "v0"
    assert db.has_undo("user", "proj", "index.yml") is False
    assert db.has_redo("user", "proj", "index.yml") is True


@pytest.mark.regression
def test_undo_never_touches_archive(db):
    """Undo is a pure preview: Archive keeps whatever the last real save
    left it at, regardless of what undo/redo has previewed since."""
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")

    db.undo_project_file("user", "proj", "index.yml", "v1")

    assert db.get_archive("proj", "index.yml") == "v1"
    assert db.list_archives("proj") == ["index.yml"]


@pytest.mark.contract
def test_redo_with_nothing_to_redo_is_a_noop(db):
    db.save_project_file("user", "proj", "index.yml", "v0")

    assert db.redo_project_file("user", "proj", "index.yml", "v0") is None
    assert db.get_archive("proj", "index.yml") == "v0"


@pytest.mark.regression
def test_redo_replays_undone_content_and_enables_undo_again(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")
    db.undo_project_file("user", "proj", "index.yml", "v1")  # now previewing "v0"

    replayed = db.redo_project_file("user", "proj", "index.yml", "v0")  # editor currently shows "v0"

    assert replayed == "v1"
    assert db.has_undo("user", "proj", "index.yml") is True
    assert db.has_redo("user", "proj", "index.yml") is False
    # Neither undo nor redo ever touched Archive — still whatever the
    # real save left it at.
    assert db.get_archive("proj", "index.yml") == "v1"


@pytest.mark.regression
def test_multiple_undo_then_multiple_redo_walk_the_full_trail(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")
    db.save_project_file("user", "proj", "index.yml", "v2")

    # Each call passes whatever the editor is currently previewing —
    # exactly what the previous call just returned.
    assert db.undo_project_file("user", "proj", "index.yml", "v2") == "v1"
    assert db.undo_project_file("user", "proj", "index.yml", "v1") == "v0"
    assert db.undo_project_file("user", "proj", "index.yml", "v0") is None  # nothing before v0

    assert db.redo_project_file("user", "proj", "index.yml", "v0") == "v1"
    assert db.redo_project_file("user", "proj", "index.yml", "v1") == "v2"
    assert db.redo_project_file("user", "proj", "index.yml", "v2") is None  # nothing past v2
    # Archive was never touched by any of the above.
    assert db.get_archive("proj", "index.yml") == "v2"


@pytest.mark.regression
def test_history_is_scoped_per_user(db):
    db.save_project_file("alice", "proj", "index.yml", "alice-v0")
    db.save_project_file("alice", "proj", "index.yml", "alice-v1")

    # bob never saved index.yml himself — his own undo stack is empty
    # even though the file has history for alice.
    assert db.has_undo("bob", "proj", "index.yml") is False
    assert db.undo_project_file("bob", "proj", "index.yml", "whatever bob has open") is None
    # alice's own trail is untouched by bob's no-op.
    assert db.has_undo("alice", "proj", "index.yml") is True


@pytest.mark.regression
def test_clear_history_removes_every_files_history_for_the_project(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")
    db.save_project_file("user", "proj", "notes.txt", "n0")
    db.save_project_file("user", "proj", "notes.txt", "n1")

    db.clear_history("user", "proj")

    assert db.has_undo("user", "proj", "index.yml") is False
    assert db.has_undo("user", "proj", "notes.txt") is False
    # Clearing history never touches current content.
    assert db.get_archive("proj", "index.yml") == "v1"
    assert db.get_archive("proj", "notes.txt") == "n1"


@pytest.mark.regression
def test_clear_history_is_scoped_to_its_own_project(db):
    db.save_project_file("user", "proj-a", "index.yml", "a-v0")
    db.save_project_file("user", "proj-a", "index.yml", "a-v1")
    db.save_project_file("user", "proj-b", "index.yml", "b-v0")
    db.save_project_file("user", "proj-b", "index.yml", "b-v1")

    db.clear_history("user", "proj-a")

    assert db.has_undo("user", "proj-a", "index.yml") is False
    assert db.has_undo("user", "proj-b", "index.yml") is True


@pytest.mark.regression
def test_clear_history_is_scoped_to_its_own_user(db):
    db.save_project_file("alice", "proj", "index.yml", "v0")
    db.save_project_file("alice", "proj", "index.yml", "v1")
    db.save_project_file("bob", "proj", "index.yml", "v0")
    db.save_project_file("bob", "proj", "index.yml", "v1")

    db.clear_history("alice", "proj")

    assert db.has_undo("alice", "proj", "index.yml") is False
    assert db.has_undo("bob", "proj", "index.yml") is True


@pytest.mark.regression
def test_get_archives_returns_only_current_content(db):
    db.save_project_files("proj", {"index.yml": "v0", "notes.txt": "n0"})
    db.save_project_files("proj", {"index.yml": "v1"})

    assert db.get_archives("proj") == {"index.yml": "v1", "notes.txt": "n0"}


@pytest.mark.regression
def test_get_archives_is_scoped_to_its_own_project(db):
    db.save_project_files("proj-a", {"index.yml": "a"})
    db.save_project_files("proj-b", {"index.yml": "b"})

    assert db.get_archives("proj-a") == {"index.yml": "a"}


@pytest.mark.regression
def test_delete_archive_removes_it_and_its_history(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")

    db.delete_archive("proj", "index.yml")

    assert db.get_archive("proj", "index.yml") is None
    assert db.has_undo("user", "proj", "index.yml") is False


@pytest.mark.regression
def test_delete_archives_removes_every_file_and_all_their_history(db):
    db.save_project_file("user", "proj", "index.yml", "v0")
    db.save_project_file("user", "proj", "index.yml", "v1")
    db.save_project_file("user", "proj", "notes.txt", "n0")

    db.delete_archives("proj")

    assert db.get_archives("proj") == {}
    assert db.list_archives("proj") == []
    assert db.has_undo("user", "proj", "index.yml") is False
