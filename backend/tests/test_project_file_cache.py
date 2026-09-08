"""The byte-bounded LRU every project file is read through.

What it has to get right is not "does it remember": it is what it drops,
and when. An entry count would not bound memory when the entries are a
project's own files, and a key shared between a stored project and a
package would serve one product's bytes for another's path.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from tracking.project_files import (
    CachedProjectFiles, DbProjectFiles, PackageProjectFiles, ProjectFileCache, ProjectFiles,
    project_files_for,
)


class _Reader(ProjectFiles):
    """A reader that counts how often it is actually asked."""

    def __init__(self, files: dict[str, bytes], scope: str = "s") -> None:
        self.files = files
        self.reads: list[str] = []
        self._scope = scope

    def resolve(self, name: str) -> str | None:
        return name if name in self.files else None

    def cache_key(self, path: str) -> str:
        return f"{self._scope}\0{path}"

    def read(self, path: str) -> tuple[bytes, str] | None:
        self.reads.append(path)
        content = self.files.get(path)
        return None if content is None else (content, "text/plain")


def _automaton(project_id: str = "p", revision: int | None = None) -> Automaton:
    init_action = Action(name="init", ui_label="init", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action, states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False,
        project_id=project_id,
    )
    if revision is not None:
        automaton.set_storage_location(revision)
    return automaton


def test_a_second_read_of_the_same_file_never_reaches_the_reader():
    inner = _Reader({"a.txt": b"hello"})
    files = CachedProjectFiles(inner, ProjectFileCache(1000))

    assert files.read("a.txt") == (b"hello", "text/plain")
    assert files.read("a.txt") == (b"hello", "text/plain")
    assert inner.reads == ["a.txt"]


def test_a_file_that_is_not_there_is_asked_for_again():
    """A miss is not a value: a project that gains the file it was
    missing must not wait for an eviction to be seen."""
    inner = _Reader({})
    files = CachedProjectFiles(inner, ProjectFileCache(1000))

    assert files.read("a.txt") is None
    inner.files["a.txt"] = b"now here"
    assert files.read("a.txt") == (b"now here", "text/plain")


def test_the_bound_is_bytes_and_the_least_recently_read_file_goes_first():
    inner = _Reader({"a": b"x" * 10, "b": b"y" * 10, "c": b"z" * 10})
    files = CachedProjectFiles(inner, ProjectFileCache(25))

    files.read("a")
    files.read("b")
    files.read("a")     # 'a' is now the most recently used, 'b' the least
    files.read("c")     # 30 bytes wanted, 25 allowed -> 'b' evicted

    assert inner.reads == ["a", "b", "c"]
    files.read("a")
    files.read("c")
    assert inner.reads == ["a", "b", "c"]
    files.read("b")
    assert inner.reads == ["a", "b", "c", "b"]


def test_a_file_bigger_than_the_whole_bound_is_served_but_never_kept():
    """Evicting everything for something that still would not fit buys
    nothing — and would make one oversized file empty the cache on every
    single read of it."""
    inner = _Reader({"small": b"s" * 10, "huge": b"h" * 100})
    files = CachedProjectFiles(inner, ProjectFileCache(50))

    files.read("small")
    assert files.read("huge") == (b"h" * 100, "text/plain")
    assert files.read("huge") == (b"h" * 100, "text/plain")
    files.read("small")

    assert inner.reads == ["small", "huge", "huge"]


def test_resizing_down_evicts_immediately():
    cache = ProjectFileCache(1000)
    inner = _Reader({"a": b"x" * 400, "b": b"y" * 400})
    files = CachedProjectFiles(inner, cache)
    files.read("a")
    files.read("b")

    cache.resize(500)

    files.read("b")
    assert inner.reads == ["a", "b"]
    files.read("a")
    assert inner.reads == ["a", "b", "a"]


def test_forgetting_a_project_drops_its_files_at_every_revision_and_nobody_elses(db):
    """What a save does: a draft revision is rewritten in place, so the
    same (project, revision, path) can hold new bytes."""
    cache = ProjectFileCache(10000)
    db.ensure_project("mine")
    db.ensure_project("other")
    db.save_project_files("mine", {"a.txt": b"first"}, {"a.txt": "text/plain"})
    db.save_project_files("other", {"a.txt": b"theirs"}, {"a.txt": "text/plain"})
    mine = CachedProjectFiles(DbProjectFiles(db, _automaton("mine", db.get_project_revision("mine"))), cache)
    theirs = CachedProjectFiles(DbProjectFiles(db, _automaton("other", db.get_project_revision("other"))), cache)

    assert mine.read("a.txt")[0] == b"first"
    assert theirs.read("a.txt")[0] == b"theirs"

    db.save_project_files("mine", {"a.txt": b"second"}, {"a.txt": "text/plain"})
    assert mine.read("a.txt")[0] == b"first", "still cached until something says otherwise"

    cache.forget_project("mine")
    assert mine.read("a.txt")[0] == b"second"
    assert theirs.read("a.txt")[0] == b"theirs"


def test_a_package_and_a_stored_project_never_share_a_key(tmp_path, db):
    """Neither key carries a slot the other leaves empty — and neither can
    ever be mistaken for the other."""
    db.ensure_project("p")
    db.save_project_files("p", {"a.txt": b"from the database"}, {"a.txt": "text/plain"})
    stored = DbProjectFiles(db, _automaton("p", db.get_project_revision("p")))
    (tmp_path / "a.txt").write_bytes(b"from the package")
    packaged = PackageProjectFiles(tmp_path)

    assert stored.cache_key("a.txt") != packaged.cache_key("a.txt")

    cache = ProjectFileCache(10000)
    assert CachedProjectFiles(stored, cache).read("a.txt")[0] == b"from the database"
    assert CachedProjectFiles(packaged, cache).read("a.txt")[0] == b"from the package"


def test_both_real_readers_are_composed_behind_the_cache_and_the_third_is_not(tmp_path, db):
    db.ensure_project("p")
    db.save_project_files("p", {"a.txt": b"x"}, {"a.txt": "text/plain"})
    stored = project_files_for(db, _automaton("p", db.get_project_revision("p")))
    assert isinstance(stored, CachedProjectFiles)

    packaged_automaton = _automaton("p")
    packaged_automaton.archives_dir = tmp_path
    assert isinstance(project_files_for(None, packaged_automaton), CachedProjectFiles)

    # Nothing to read, and so nothing to cache: an automaton built in
    # memory by a test has neither a revision nor files of its own.
    nothing = project_files_for(db, _automaton("p"))
    assert not isinstance(nothing, CachedProjectFiles)
    assert nothing.read("a.txt") is None
    with pytest.raises(NotImplementedError):
        nothing.cache_key("a.txt")
