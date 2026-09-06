"""Integration coverage for the invariant AutomatonLoader.invalidate/
invalidate_cache/set_cached exist to serve: `Db.write_archive_at_revision`
(and friends) know nothing about the loader, so every real caller that
overwrites a project's stored files is responsible for telling it —
ProjectEditor at a file save (finalize_update's own set_cached call),
ProjectManager.revert_to_published, and a re-upload (put_project /
_persist_uploaded_project, which also goes through finalize_update).
Each test here corrupts a revision directly (bypassing real save-path
validation, same technique test_project_health.py's own
_corrupt_published_revision uses), forces a real build attempt so the
loader genuinely caches the failure, then exercises the real path and
confirms the project builds again with no leftover stale failure.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton_builder import AutomatonBuilder
from db.models import Archive
from project.project_service import ProjectService

pytestmark = pytest.mark.regression

USERNAME = "user"

VALID_YML = """
project:
  id: {project_id}
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

BROKEN_YML = "not: [valid, yaml: at all"


async def _commit(_project_id, _automaton) -> None:
    pass


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


def _upload(db, project_service: ProjectService, project_id: str, yml: str | None = None) -> None:
    content = (yml or VALID_YML).format(project_id=project_id).encode("utf-8")
    asyncio.run(project_service.put_project(content, "text/yaml", _commit))


def _corrupt_in_place(db, project_id: str, revision: int) -> None:
    """Overwrites `revision`'s own stored index.yml with something that
    doesn't build, without bumping the revision — exactly the "same
    revision, in place" case a normal save-path can never produce (it
    always forks off a published revision) but a stale cache could still
    be left holding onto."""
    Archive.update(content=BROKEN_YML.encode("utf-8")).where(
        (Archive.project == project_id) & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()


def test_a_real_save_on_a_previously_broken_draft_revision_clears_the_stale_failure(db, project_service):
    project_id = "wip_fix"
    _upload(db, project_service, project_id)  # never published: draft stays at revision 0 in place
    revision = db.get_project_revision(project_id)
    _corrupt_in_place(db, project_id, revision)
    automaton_loader = project_service._manager._automaton_loader
    automaton_loader.invalidate_cache(project_id)  # simulates a fresh process never having cached the old, valid build

    with pytest.raises(Exception):
        automaton_loader.load(project_id)
    assert (project_id, revision) in automaton_loader._build_failures

    fixed_automaton = AutomatonBuilder().build({"index.yml": VALID_YML.format(project_id=project_id)})
    asyncio.run(project_service._manager.finalize_update(project_id, fixed_automaton, _commit))

    assert (project_id, revision) not in automaton_loader._build_failures
    healed = automaton_loader.load(project_id)
    assert healed.project_id == project_id


def test_revert_to_published_clears_a_broken_drafts_stale_failure(db, project_service):
    project_id = "wip_revert"
    _upload(db, project_service, project_id)
    project_service.publish_project(project_id)
    published_revision = db.get_project_published_revision(project_id)

    # A raw draft edit, bypassing every real save path's own validation —
    # exactly what a user mid-edit (who then gives up) looks like. Forks
    # off the published revision first (same as any real edit after a
    # publish), so the corruption lands on a brand new draft revision.
    db.save_project_files(project_id, {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})
    draft_revision = db.get_project_revision(project_id)
    assert draft_revision != published_revision
    automaton_loader = project_service._manager._automaton_loader
    automaton_loader.invalidate_cache(project_id)
    with pytest.raises(Exception):
        automaton_loader.load(project_id)
    assert (project_id, draft_revision) in automaton_loader._build_failures

    asyncio.run(project_service.revert_to_published(project_id, _commit))

    assert (project_id, draft_revision) not in automaton_loader._build_failures
    healed = automaton_loader.load(project_id)
    assert healed.project_id == project_id


def test_reuploading_a_project_clears_its_previously_broken_published_revisions_stale_failure(db, project_service):
    project_id = "wip_reimport"
    _upload(db, project_service, project_id)
    published_revision = db.get_project_published_revision(project_id)
    automaton_loader = project_service._manager._automaton_loader
    automaton_loader.invalidate_cache(project_id)

    # The published revision breaks under a framework upgrade — discovered
    # on the next cache miss, same scenario test_project_health.py's own
    # _corrupt_published_revision covers for recompute_availability; here
    # the fix is a real re-upload (import), not just a recompute.
    _corrupt_in_place(db, project_id, published_revision)
    with pytest.raises(Exception):
        automaton_loader.load_at_revision(project_id, published_revision)
    assert (project_id, published_revision) in automaton_loader._build_failures

    _upload(db, project_service, project_id, VALID_YML + "\n")  # a fresh, valid revision on top

    new_published_revision = db.get_project_published_revision(project_id)
    assert new_published_revision > published_revision
    healed = automaton_loader.load(project_id)
    assert healed.project_id == project_id
    # The old, still-broken revision's own cached failure is irrelevant
    # now (no session/draft/publish points at it any more) — what matters
    # is that the *current* one builds, with nothing left over blocking it.
    assert automaton_loader.load_at_revision(project_id, new_published_revision).project_id == project_id
