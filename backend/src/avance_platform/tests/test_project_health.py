"""ProjectHealthChecker/ProjectManager's own "a project's revision doesn't
build anymore" story: the published revision's own health drives
is_paused (never the draft alone), the draft's own health only gates the
design-view's automaton-derived endpoints (ensure_project_not_broken), and
and a project's state is read off its health every time it is asked for
— there is no notification standing between the two to go stale.
"""
from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest

from avance_platform.platform_service import PlatformService

from automaton.automaton_builder import AutomatonBuilder
from turn.sessions.session_manager import SessionManager
from project.archive.automaton_loader import AutomatonLoader
from project.health import ProjectHealthChecker
from project.project_service import ProjectService
from system.service_error import ServiceError
from conftest import rewrite_archive_content

pytestmark = pytest.mark.contract

USERNAME = "user"
BROKEN_YML = "not: [valid, yaml: at all"

VALID_YML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def _publish(db, project_service: ProjectService, project_id: str, index_yml: str) -> None:
    if "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_id}\n{index_yml}"
    is_new_project = not db.project_exists(project_id)
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)
    db.set_active_project_id(project_id, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})


    asyncio.run(project_service.manager.finalize_update(project_id, automaton, is_new_project=is_new_project))


def _corrupt_published_revision(db, project_service: ProjectService, project_id: str) -> None:
    """Simulates a framework upgrade breaking a previously-healthy
    published revision: the stored index.yml is overwritten in place, at
    the exact revision already published, with nothing that builds under
    today's rules — no save-time validation runs, since a real save path
    would reject this outright. Also drops the AutomatonLoader's own
    cached (still-valid) Automaton for that revision — _publish already
    populated it, and a real process would only ever see this corruption
    on a fresh cache miss (a new boot, an evicted/never-cached revision),
    never on one it already built successfully earlier in its own lifetime."""
    revision = db.get_project_published_revision(project_id)
    rewrite_archive_content(project_id, "index.yml", revision, BROKEN_YML.encode("utf-8"))
    project_service.automaton_loader.invalidate_cache(project_id)


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db, AutomatonLoader(db), SessionManager(db))


def _make_admin(db, user_id: str) -> None:
    db.get_or_create_user("test", f"sub-{user_id}", user_id, user_id, None, user_id=user_id)
    db.set_user_role(user_id, "admin")



def test_a_broken_published_revision_pauses_with_the_builders_own_message(db, project_service):
    _publish(db, project_service, "broken", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken")

    project_service.recompute_availability("broken")

    is_paused, reason = db.get_project_availability("broken")
    assert is_paused is True
    assert "broken" in reason and "index.yml no longer builds" in reason


def test_a_broken_draft_with_a_healthy_published_revision_never_pauses(db, project_service):
    _publish(db, project_service, "wip", VALID_YML)
    db.save_project_files("wip", {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})

    project_service.recompute_availability("wip")

    assert db.get_project_availability("wip") == (False, None)


def test_a_publish_that_builds_again_resumes_the_project(db, project_service):
    _publish(db, project_service, "flaky", VALID_YML)
    _corrupt_published_revision(db, project_service, "flaky")
    project_service.recompute_availability("flaky")
    assert db.get_project_availability("flaky")[0] is True

    _publish(db, project_service, "flaky", VALID_YML)

    assert db.get_project_availability("flaky") == (False, None)



def test_ensure_project_not_broken_is_a_noop_for_a_healthy_draft(db, project_service):
    _publish(db, project_service, "solo", VALID_YML)
    project_service.ensure_project_not_broken("solo")


def test_ensure_project_not_broken_raises_409_project_broken_for_a_broken_draft(db, project_service):
    _publish(db, project_service, "wip", VALID_YML)
    db.save_project_files("wip", {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})

    with pytest.raises(ServiceError) as exc_info:
        project_service.ensure_project_not_broken("wip")

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "project_broken"



def test_set_manually_running_rejects_a_project_whose_published_revision_is_broken(db, project_service):
    _publish(db, project_service, "solo", VALID_YML)
    PlatformService(project_service).set_manually_paused("solo")
    _corrupt_published_revision(db, project_service, "solo")

    with pytest.raises(ServiceError) as exc_info:
        PlatformService(project_service).set_manually_running("solo")

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "project_broken"
    assert db.get_manually_paused("solo") is True



def test_get_runtime_status_reports_broken_published_and_draft_separately(db, project_service):
    _publish(db, project_service, "healthy", VALID_YML)
    _publish(db, project_service, "broken_pub", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken_pub")
    _publish(db, project_service, "broken_draft", VALID_YML)
    db.save_project_files("broken_draft", {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})

    rows = {row["id"]: row for row in PlatformService(project_service).get_runtime_status()}

    assert rows["healthy"]["broken"] == {"published": None, "draft": None}
    assert rows["broken_pub"]["broken"]["published"] is not None
    assert rows["broken_draft"]["broken"]["published"] is None
    assert rows["broken_draft"]["broken"]["draft"] is not None


def test_a_read_only_poll_never_changes_what_a_recompute_then_finds(db, project_service):
    """Asking for the runtime status is a read: whatever it caches for
    itself, the recompute after it still sees the project as it is (see
    ProjectHealthChecker's own check() vs current() split)."""
    _publish(db, project_service, "flaky", VALID_YML)

    _corrupt_published_revision(db, project_service, "flaky")
    PlatformService(project_service).get_runtime_status()
    PlatformService(project_service).get_runtime_status()
    project_service.recompute_availability("flaky")

    assert db.get_project_availability("flaky")[0] is True



def test_recompute_all_availability_pauses_only_the_broken_project(db, project_service):
    _publish(db, project_service, "healthy", VALID_YML)
    _publish(db, project_service, "broken", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken")

    project_service.recompute_all_availability()

    assert db.get_project_availability("healthy") == (False, None)
    assert db.get_project_availability("broken")[0] is True



def test_a_broken_published_revision_reports_where_it_broke(db, project_service):
    _publish(db, project_service, "broken", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken")

    published = ProjectHealthChecker(db, project_service.automaton_loader).current("broken").published

    assert published.error is not None
    assert published.file == "index.yml"
    assert published.line == 0


DEP_YML = """
project:
  id: dep
  family: fam5
env:
  flag:
    value: "'x'"
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

WATCHER_YML = """
project:
  id: watcher_a
  family: fam5
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: notice
        target: a
        trigger: "automaton.dep.env.flag == 'x'"
"""


def test_a_stale_build_failure_that_depended_on_a_deleted_and_recreated_project_clears_itself(
    db, project_service,
):
    """watcher_a's own self-loop trigger references automaton.dep.env.flag
    — resolved at *build* time against whichever projects currently exist
    in its own family (AutomatonLoader.known_projects_env_keys), not just
    checked for runtime availability. Deleting 'dep' and forcing a fresh
    build of watcher_a while it's gone makes that build genuinely fail
    (not just "unavailable") — AutomatonLoader caches that failure per
    (project_id, revision), keyed on watcher_a alone, with nothing
    watching for 'dep' to come back. Recreating 'dep' must still heal
    watcher_a without a restart (see ProjectManager._recheck_dependents_
    of_changed_id's own clear_all_build_failures call)."""
    _publish(db, project_service, "dep", DEP_YML)
    _publish(db, project_service, "watcher_a", WATCHER_YML)
    assert db.get_project_availability("watcher_a") == (False, None)


    asyncio.run(project_service.manager.delete_project("dep"))
    project_service.recompute_availability("watcher_a")
    assert db.get_project_availability("watcher_a")[0] is True
    project_service.automaton_loader.invalidate_cache("watcher_a")
    rows = {row["id"]: row for row in PlatformService(project_service).get_runtime_status()}
    assert "automaton.dep" in rows["watcher_a"]["broken"]["published"]

    _publish(db, project_service, "dep", DEP_YML)
    is_paused, reason = db.get_project_availability("watcher_a")
    assert is_paused is False
    assert reason is None



def test_a_lazy_load_failure_on_the_published_revision_pauses_the_project(db, project_service):
    """AutomatonLoader has no reference to ProjectManager — the only way
    a build failure discovered by some unrelated read (never a save/
    publish/boot sweep) reaches recompute_availability is the
    ProjectRevisionBuildFailed event (see register_availability_cascade)."""
    from automaton.build_error import AutomatonBuildError

    _publish(db, project_service, "flaky", VALID_YML)
    project_service.register_availability_cascade()
    _corrupt_published_revision(db, project_service, "flaky")
    assert db.get_project_availability("flaky") == (False, None)

    with pytest.raises(AutomatonBuildError):
        project_service.get_automaton("flaky", db.get_project_published_revision("flaky"))

    is_paused, reason = db.get_project_availability("flaky")
    assert is_paused is True
    assert "index.yml no longer builds" in reason



TOOLS_FIELD_YML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    tools: [pino]
"""


def test_boot_sweep_never_rewrites_an_archived_revision_using_the_old_tools_field(db, project_service):
    """Stands in for main.py's own boot sequence (Db(...) then
    register_availability_cascade(), then recompute_all_availability(),
    with nothing in between touching stored revisions anymore — see
    PROJECT_SPECS.md §8's own note): an already-published revision still
    declaring the long-removed `tools:` field is never rewritten, only
    paused, with the builder's own message surfaced as a project_broken
    SystemWarning — exactly once, not twice. The cascade being wired up
    (as it really is at boot) is what makes this a regression test for
    ProjectManager's own reentrancy guard: recompute_all_availability's
    own recompute_availability call discovers the broken build inside
    ProjectHealthChecker.check(), which publishes ProjectRevisionBuildFailed
    synchronously — with no guard, that reenters recompute_availability for
    the same project before the outer call's own previous_health/_last
    bookkeeping settles, firing ProjectPublishedHealthChanged (and so this
    SystemWarning/push) twice for one real transition."""
    _publish(db, project_service, "old_format", VALID_YML)
    revision = db.get_project_published_revision("old_format")
    rewrite_archive_content("old_format", "index.yml", revision, TOOLS_FIELD_YML.encode("utf-8"))
    project_service.automaton_loader.invalidate_cache("old_format")
    before = db.get_archive("old_format", "index.yml", revision=revision)

    project_service.register_availability_cascade()

    project_service.recompute_all_availability()

    after = db.get_archive("old_format", "index.yml", revision=revision)
    assert after == before

    is_paused, reason = db.get_project_availability("old_format")
    assert is_paused is True
    assert "'tools' is no longer a valid field" in reason and "ai-may-read-sources" in reason
