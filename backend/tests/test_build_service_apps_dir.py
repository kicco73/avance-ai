"""A build publishes a package, in one step that either happened or
didn't.

What matters here is not that the compiler works — that has its own
verification — but the order: assemble where nothing can load it, prove
it imports, make it visible with a single rename, drop the stale cache
entry, then delete what came before. Every assertion below is about a
moment in that order.
"""
from __future__ import annotations

import pytest

from automaton.automaton import CompiledAutomaton
from build.apps import package_dir, staging_dir
from build.build_service import BuildService, module_name_for
from build.compiler import CompileError
from db import Db
from project.archive.compiled_automaton_loader import CompiledAutomatonLoader

PROJECT_ID = "demo"
INDEX = """
project:
  id: demo
init-action:
  target: start
general-prompt: hello
states:
  start:
    contextual-prompt: go
"""


class _Service:
    """Only the two things BuildService actually asks a ProjectService."""

    def __init__(self, db: Db) -> None:
        self._db = db
        self.invalidated: list[tuple[str, int]] = []

    def get_published_revision(self, project_id: str) -> int:
        revision = self._db.get_project_published_revision(project_id)
        if revision is None:
            raise ValueError(f"Project '{project_id}' has never been published.")
        return revision

    def invalidate_automaton(self, project_id: str, revision: int) -> None:
        self.invalidated.append((project_id, revision))


def _save(db: Db, index: str = INDEX) -> None:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": index.encode()}, {"index.yml": "text/yaml"})


def _published(db: Db, index: str = INDEX) -> int:
    _save(db, index)
    db.publish_project(PROJECT_ID)
    return db.get_project_revision(PROJECT_ID)


def test_a_build_leaves_exactly_one_package_where_the_loader_looks(db, tmp_path):
    revision = _published(db)
    service = _Service(db)

    result = BuildService(db, service, tmp_path).build_local_module(PROJECT_ID)

    expected = package_dir(tmp_path, module_name_for(PROJECT_ID), revision)
    assert result["path"] == str(expected)
    assert result["revision"] == revision
    assert sorted(path.name for path in tmp_path.iterdir()) == [expected.name]
    assert not staging_dir(tmp_path, module_name_for(PROJECT_ID), revision).exists()
    assert service.invalidated == [(PROJECT_ID, revision)]

    # And the loader picks it up without being told anything.
    automaton = CompiledAutomatonLoader(db, tmp_path).load_at_revision(PROJECT_ID, revision)
    assert isinstance(automaton, CompiledAutomaton)


def test_building_again_at_a_new_revision_replaces_the_old_package(db, tmp_path):
    first = _published(db)
    BuildService(db, _Service(db), tmp_path).build_local_module(PROJECT_ID)
    second = _published(db, INDEX.replace("hello", "goodbye"))
    assert second != first

    BuildService(db, _Service(db), tmp_path).build_local_module(PROJECT_ID)

    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == [package_dir(tmp_path, module_name_for(PROJECT_ID), second).name]


def test_a_project_with_unpublished_changes_cannot_be_built(db, tmp_path):
    _published(db)
    _save(db, INDEX.replace("hello", "an unpublished edit"))

    with pytest.raises(CompileError, match="publish"):
        BuildService(db, _Service(db), tmp_path).build_local_module(PROJECT_ID)

    assert list(tmp_path.iterdir()) == [], "a refused build writes nothing at all"


def test_a_failed_build_leaves_nothing_the_loader_could_pick_up(db, tmp_path, monkeypatch):
    revision = _published(db)
    import build.build_service as build_service

    def explode(*args, **kwargs):
        raise RuntimeError("compiler blew up halfway")

    monkeypatch.setattr(build_service, "compile_contents", explode)

    with pytest.raises(RuntimeError):
        BuildService(db, _Service(db), tmp_path).build_local_module(PROJECT_ID)

    assert list(tmp_path.iterdir()) == []
    assert type(CompiledAutomatonLoader(db, tmp_path).load_at_revision(PROJECT_ID, revision)).__name__ == "Automaton"


def test_a_package_that_does_not_import_is_never_published(db, tmp_path, monkeypatch):
    """The proof happens in staging: a build that cannot load is a failed
    build, not something to rename into place."""
    revision = _published(db)
    import build.build_service as build_service
    from build.apps import PackageError

    def refuse(*args, **kwargs):
        raise PackageError("nope")

    monkeypatch.setattr(build_service, "import_automaton", refuse)

    with pytest.raises(CompileError, match="does not load"):
        BuildService(db, _Service(db), tmp_path).build_local_module(PROJECT_ID)

    assert list(tmp_path.iterdir()) == []
