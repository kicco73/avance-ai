from __future__ import annotations

from db import Archive


def test_next_project_version_starts_at_zero_for_a_new_project(db):
    assert db.next_project_version("proj") == 0


def test_first_save_creates_version_0(db):
    version = db.save_project_version("proj", {"index.yml": "v0"})
    assert version == 0
    assert db.get_archive("proj", "index.yml") == ("v0", 0)


def test_next_project_version_increments_after_each_save(db):
    db.save_project_version("proj", {"index.yml": "v0"})
    assert db.next_project_version("proj") == 1
    db.save_project_version("proj", {"index.yml": "v1"})
    assert db.next_project_version("proj") == 2


def test_every_file_saved_together_gets_the_same_version(db):
    version = db.save_project_version("proj", {"index.yml": "yml v0", "notes.txt": "notes v0"})

    assert db.get_archive("proj", "index.yml") == ("yml v0", version)
    assert db.get_archive("proj", "notes.txt") == ("notes v0", version)


def test_a_later_save_of_one_file_bumps_the_shared_project_version(db):
    db.save_project_version("proj", {"index.yml": "v0", "notes.txt": "v0"})
    # Saving index.yml alone still advances the *project's* version —
    # there is no independent per-file counter anymore.
    version = db.save_project_version("proj", {"index.yml": "v1"})

    assert version == 1
    assert db.get_archive("proj", "index.yml") == ("v1", 1)


def test_save_project_version_never_accepts_a_caller_supplied_version(db):
    """The version number is always computed internally — there is no
    parameter a caller could use to target/overwrite an earlier one."""
    import inspect

    params = inspect.signature(db.save_project_version).parameters
    assert "version" not in params


def test_each_project_has_its_own_independent_version_sequence(db):
    db.save_project_version("proj-a", {"index.yml": "a-v0"})
    db.save_project_version("proj-b", {"index.yml": "b-v0"})
    db.save_project_version("proj-b", {"index.yml": "b-v1"})

    assert db.get_archive("proj-a", "index.yml") == ("a-v0", 0)
    assert db.get_archive("proj-b", "index.yml") == ("b-v1", 1)


def test_get_archive_returns_none_for_an_unknown_file(db):
    assert db.get_archive("proj", "missing.yml") is None


def test_get_archive_at_an_exact_stored_version(db):
    db.save_project_version("proj", {"index.yml": "v0"})
    db.save_project_version("proj", {"index.yml": "v1"})

    assert db.get_archive("proj", "index.yml", version=0) == ("v0", 0)
    assert db.get_archive("proj", "index.yml", version=1) == ("v1", 1)


def test_get_archive_at_a_version_with_no_exact_row_returns_none(db):
    """No more "highest not exceeding" fallback: a version that was never
    actually saved for this file is a miss, not silently clamped to the
    nearest older one. save_project_version can't construct a gap through
    normal use (every file always advances together) — this fixture uses
    the model directly to prove get_archive's read path is still exact
    even if one somehow existed."""
    Archive.create(project_name="proj", archive_name="index.yml", version=0, content="v0")
    Archive.create(project_name="proj", archive_name="index.yml", version=2, content="v2")  # a gap at version 1

    assert db.get_archive("proj", "index.yml", version=1) is None
    assert db.get_archive("proj", "index.yml", version=2) == ("v2", 2)


def test_get_archive_above_the_latest_version_returns_none(db):
    db.save_project_version("proj", {"index.yml": "v0"})

    assert db.get_archive("proj", "index.yml", version=99) is None


def test_get_archive_below_every_stored_version_returns_none(db):
    Archive.create(project_name="proj", archive_name="index.yml", version=5, content="v5")

    assert db.get_archive("proj", "index.yml", version=0) is None
    assert db.get_archive("proj", "index.yml", version=-1) is None


def test_count_archive_versions(db):
    assert db.count_archive_versions("proj", "index.yml") == 0
    db.save_project_version("proj", {"index.yml": "v0"})
    db.save_project_version("proj", {"index.yml": "v1"})
    assert db.count_archive_versions("proj", "index.yml") == 2


def test_get_archives_returns_only_the_latest_version_per_file(db):
    db.save_project_version("proj", {"index.yml": "v0", "notes.txt": "notes v0"})
    db.save_project_version("proj", {"index.yml": "v1"})

    assert db.get_archives("proj") == {"index.yml": "v1", "notes.txt": "notes v0"}


def test_get_archives_is_scoped_to_its_own_project(db):
    db.save_project_version("proj-a", {"index.yml": "a"})
    db.save_project_version("proj-b", {"index.yml": "b"})

    assert db.get_archives("proj-a") == {"index.yml": "a"}


def test_list_archives_does_not_repeat_a_name_per_version(db):
    db.save_project_version("proj", {"index.yml": "v0"})
    db.save_project_version("proj", {"index.yml": "v1"})
    db.save_project_version("proj", {"index.yml": "v2"})

    assert db.list_archives("proj") == ["index.yml"]


def test_prune_archive_history_keeps_only_the_latest_version_of_every_file(db):
    db.save_project_version("proj", {"index.yml": "v0", "notes.txt": "notes v0"})
    db.save_project_version("proj", {"index.yml": "v1"})

    db.prune_archive_history("proj")

    assert db.count_archive_versions("proj", "index.yml") == 1
    assert db.get_archive("proj", "index.yml") == ("v1", 1)
    # notes.txt was never re-saved at version 1 in this test — pruning
    # still leaves it at whichever row is its own latest.
    assert db.count_archive_versions("proj", "notes.txt") == 1


def test_prune_archive_history_is_scoped_to_its_own_project(db):
    db.save_project_version("proj-a", {"index.yml": "a-v0"})
    db.save_project_version("proj-a", {"index.yml": "a-v1"})
    db.save_project_version("proj-b", {"index.yml": "b-v0"})
    db.save_project_version("proj-b", {"index.yml": "b-v1"})

    db.prune_archive_history("proj-a")

    assert db.count_archive_versions("proj-a", "index.yml") == 1
    assert db.count_archive_versions("proj-b", "index.yml") == 2


def test_delete_archive_removes_every_version(db):
    db.save_project_version("proj", {"index.yml": "v0"})
    db.save_project_version("proj", {"index.yml": "v1"})

    db.delete_archive("proj", "index.yml")

    assert db.count_archive_versions("proj", "index.yml") == 0
    assert db.get_archive("proj", "index.yml") is None


def test_delete_archives_removes_every_version_of_every_file(db):
    db.save_project_version("proj", {"index.yml": "v0", "notes.txt": "v0"})
    db.save_project_version("proj", {"index.yml": "v1"})

    db.delete_archives("proj")

    assert db.get_archives("proj") == {}
    assert db.list_archives("proj") == []
