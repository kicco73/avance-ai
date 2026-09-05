"""AutomatonLoader's own two build-failure fixes:

1. A broken revision is cached just like a successful one (_build_failures)
   — load_at_revision re-raises the same exception on a repeat call
   instead of rebuilding, and invalidate(project_id, revision) is the one
   place that drops both caches together for a revision whose stored
   files just got rewritten.
2. A build failure on the project's own current published/draft revision
   publishes ProjectRevisionBuildFailed — never for an older revision
   some session alone is still pinned to.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from db.models import Archive
from events import ProjectRevisionBuildFailed, subscribe
from project.archive.automaton_loader import AutomatonLoader

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"
VALID_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"
VALID_YML_2 = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi again\n"
BROKEN_YML = "not: [valid, yaml: at all"


def _publish(db, project_id: str, index_yml: str) -> None:
    if "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_id}\n{index_yml}"
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)


def _overwrite(db, project_id: str, revision: int, index_yml: str) -> None:
    Archive.update(content=index_yml.encode("utf-8")).where(
        (Archive.project == project_id) & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()


def test_a_broken_revision_loaded_twice_builds_only_once(db):
    _publish(db, PROJECT_ID, BROKEN_YML)
    loader = AutomatonLoader(db)
    revision = db.get_project_published_revision(PROJECT_ID)
    real_build = AutomatonBuilder.build
    calls: list[int] = []

    def counting_build(self, *args, **kwargs):
        calls.append(1)
        return real_build(self, *args, **kwargs)

    with patch.object(AutomatonBuilder, "build", counting_build):
        with pytest.raises(AutomatonBuildError):
            loader.load_at_revision(PROJECT_ID, revision)
        with pytest.raises(AutomatonBuildError):
            loader.load_at_revision(PROJECT_ID, revision)

    assert len(calls) == 1


def test_invalidate_lets_a_fixed_revision_build_again(db):
    _publish(db, PROJECT_ID, BROKEN_YML)
    loader = AutomatonLoader(db)
    revision = db.get_project_published_revision(PROJECT_ID)
    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, revision)

    _overwrite(db, PROJECT_ID, revision, f"project:\n  id: {PROJECT_ID}\n{VALID_YML}")

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, revision)  # still the cached failure

    loader.invalidate(PROJECT_ID, revision)

    automaton = loader.load_at_revision(PROJECT_ID, revision)  # fresh build, now succeeds
    assert automaton.project_id == PROJECT_ID


def test_invalidate_also_drops_a_cached_success(db):
    _publish(db, PROJECT_ID, VALID_YML)
    loader = AutomatonLoader(db)
    revision = db.get_project_published_revision(PROJECT_ID)
    loader.load_at_revision(PROJECT_ID, revision)  # populates the success cache

    _overwrite(db, PROJECT_ID, revision, BROKEN_YML)
    loader.invalidate(PROJECT_ID, revision)

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, revision)  # a fresh build, not the stale cached success


def test_a_broken_published_revision_publishes_the_event(db):
    _publish(db, PROJECT_ID, BROKEN_YML)
    loader = AutomatonLoader(db)
    revision = db.get_project_published_revision(PROJECT_ID)
    received: list[ProjectRevisionBuildFailed] = []
    subscribe(ProjectRevisionBuildFailed, received.append)

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, revision)

    assert received == [ProjectRevisionBuildFailed(project_id=PROJECT_ID, revision=revision)]


def test_an_old_superseded_revision_publishes_no_event(db):
    _publish(db, PROJECT_ID, VALID_YML)
    old_revision = db.get_project_published_revision(PROJECT_ID)
    _publish(db, PROJECT_ID, VALID_YML_2)  # forks + publishes a new revision on top
    assert db.get_project_published_revision(PROJECT_ID) != old_revision

    _overwrite(db, PROJECT_ID, old_revision, BROKEN_YML)
    loader = AutomatonLoader(db)
    received: list[ProjectRevisionBuildFailed] = []
    subscribe(ProjectRevisionBuildFailed, received.append)

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, old_revision)

    assert received == []
