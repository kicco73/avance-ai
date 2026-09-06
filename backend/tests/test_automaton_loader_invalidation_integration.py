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

The last group covers finalize_update's own `old_family` parameter: a
project.id can stay put while project.family alone changes (an ordinary
put_project_file edit, or a re-upload of the same id) — automaton.*
visibility is gated by family exactly like it is by id (see
AutomatonBuilder.known_projects_env_keys), so a family change needs the
exact same dependents rescan an id rename gets, and it's the one
identity change finalize_update's own id-changed branch structurally
cannot see (project.id itself never moves).
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

DEP_YML = """
project:
  id: dep
  family: {family}
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

WATCHER_YML = """
project:
  id: watcher_family
  family: fam1
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: notice
        target: a
        trigger: "automaton.dep.state == 'never'"
"""


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


# --- finalize_update's own old_family: a family-only rename ---------------


def _set_dep_family(db, project_service: ProjectService, family: str) -> None:
    """Bypasses every real save path's own cross-project validation —
    exactly the raw corruption technique the rest of this file already
    uses, needed here because a *real* save can never put a self-loop
    automaton.dep reference and a mismatched family in the store at the
    same time (AutomatonBuilder rejects the reference outright first —
    see test_project_id_metadata.py). This is the only way to reach
    "watcher_family already has a cached build failure caused by dep's
    own family" without a real edit healing it before the test even gets
    to that point. Also invalidates dep's own cache: known_projects_env_keys
    reads every *other* project's declared family through AutomatonLoader.
    _declared_meta, cached from dep's last real build — a raw archive edit
    that skips set_cached would otherwise still answer with the old family."""
    revision = db.get_project_revision("dep")
    Archive.update(content=DEP_YML.format(family=family).encode("utf-8")).where(
        (Archive.project == "dep") & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()
    project_service._manager._automaton_loader.invalidate_cache("dep")


def test_a_family_only_edit_via_put_project_file_clears_a_dependents_stale_failure(db, project_service):
    _upload(db, project_service, "dep", DEP_YML.format(family="fam1"))
    _upload(db, project_service, "watcher_family", WATCHER_YML)  # succeeds: same family, resolves automaton.dep
    automaton_loader = project_service._manager._automaton_loader

    # dep's family changes (raw, bypassing validation — see _set_dep_family)
    # to something watcher_family doesn't share, and watcher_family's own
    # cache is forced to notice: a fresh build now genuinely fails, since
    # automaton.dep no longer resolves under fam1's own known_projects_env_keys.
    _set_dep_family(db, project_service, "fam2")
    automaton_loader.invalidate_cache("watcher_family")
    with pytest.raises(Exception):
        automaton_loader.load("watcher_family")
    watcher_revision = db.get_project_revision("watcher_family")
    assert ("watcher_family", watcher_revision) in automaton_loader._build_failures

    # The real fix: an ordinary put_project_file edit that changes dep's
    # family back — project.id itself never moves, so this is exactly the
    # case finalize_update's id-rename branch structurally can't catch;
    # only its own old_family comparison can.
    asyncio.run(project_service.put_project_file(
        "dep", "index.yml", DEP_YML.format(family="fam1"), "text/yaml", _commit,
    ))

    assert ("watcher_family", watcher_revision) not in automaton_loader._build_failures
    healed = automaton_loader.load("watcher_family")
    assert healed.project_id == "watcher_family"
    assert db.get_project_availability("watcher_family") == (False, None)  # available again, no manual recompute call


def test_a_family_only_reupload_clears_a_dependents_stale_failure(db, project_service):
    _upload(db, project_service, "dep", DEP_YML.format(family="fam1"))
    _upload(db, project_service, "watcher_family", WATCHER_YML)
    automaton_loader = project_service._manager._automaton_loader

    _set_dep_family(db, project_service, "fam2")
    automaton_loader.invalidate_cache("watcher_family")
    with pytest.raises(Exception):
        automaton_loader.load("watcher_family")
    watcher_revision = db.get_project_revision("watcher_family")
    assert ("watcher_family", watcher_revision) in automaton_loader._build_failures

    # The real fix this time: a re-upload (import) of the same id, family
    # restored — put_project's own old_family capture (read before
    # _persist_uploaded_project touches anything) is what's under test.
    _upload(db, project_service, "dep", DEP_YML.format(family="fam1") + "\n")

    assert ("watcher_family", watcher_revision) not in automaton_loader._build_failures
    healed = automaton_loader.load("watcher_family")
    assert healed.project_id == "watcher_family"
