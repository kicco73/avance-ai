"""ProjectHealthChecker/ProjectManager's own "a project's revision doesn't
build anymore" story: the published revision's own health drives
is_paused (never the draft alone), the draft's own health only gates the
design-view's automaton-derived endpoints (ensure_project_not_broken), and
a real broken<->healthy transition of the published revision fires
exactly one ProjectPublishedHealthChanged event, which
ProjectHealthNotifications turns into a SystemWarning per admin plus a
best-effort ws push.
"""
from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest

from automaton.automaton_builder import AutomatonBuilder
from events import ProjectPublishedHealthChanged, publish, subscribe
from project.health_notifications import ProjectHealthNotificationJob, ProjectHealthNotifications
from project.project_service import ProjectService
from service_error import ServiceError

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

    async def commit(_project_id, _automaton):
        pass

    asyncio.run(project_service._manager.finalize_update(project_id, automaton, commit, is_new_project=is_new_project))


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
    from db.models import Archive
    revision = db.get_project_published_revision(project_id)
    Archive.update(content=BROKEN_YML.encode("utf-8")).where(
        (Archive.project == project_id) & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()
    project_service._manager._automaton_loader.invalidate_cache(project_id)


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


class FakeWsAdapter:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, dict]] = []

    async def push(self, username: str, payload: dict) -> bool:
        self.pushed.append((username, payload))
        return True


def _make_admin(db, user_id: str) -> None:
    db.get_or_create_user("test", f"sub-{user_id}", user_id, user_id, None, user_id=user_id)
    db.set_user_role(user_id, "admin")


# --- Health precedence: published drives is_paused, draft never does ----


def test_a_broken_published_revision_pauses_with_the_builders_own_message(db, project_service):
    _publish(db, project_service, "broken", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken")

    project_service.recompute_availability("broken")

    is_paused, reason = db.get_project_availability("broken")
    assert is_paused is True
    assert "broken" in reason and "index.yml no longer builds" in reason


def test_a_broken_draft_with_a_healthy_published_revision_never_pauses(db, project_service):
    _publish(db, project_service, "wip", VALID_YML)
    # A raw draft edit, bypassing every real save path's own validation —
    # exactly what a user mid-edit looks like from the DB's point of view.
    db.save_project_files("wip", {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})

    project_service.recompute_availability("wip")

    assert db.get_project_availability("wip") == (False, None)


def test_a_publish_that_builds_again_resumes_the_project(db, project_service):
    _publish(db, project_service, "flaky", VALID_YML)
    _corrupt_published_revision(db, project_service, "flaky")
    project_service.recompute_availability("flaky")
    assert db.get_project_availability("flaky")[0] is True

    _publish(db, project_service, "flaky", VALID_YML)  # a real, validated re-save + publish

    assert db.get_project_availability("flaky") == (False, None)


# --- ensure_project_not_broken (the design-view gate) --------------------


def test_ensure_project_not_broken_is_a_noop_for_a_healthy_draft(db, project_service):
    _publish(db, project_service, "solo", VALID_YML)
    project_service.ensure_project_not_broken("solo")  # must not raise


def test_ensure_project_not_broken_raises_409_project_broken_for_a_broken_draft(db, project_service):
    _publish(db, project_service, "wip", VALID_YML)
    db.save_project_files("wip", {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})

    with pytest.raises(ServiceError) as exc_info:
        project_service.ensure_project_not_broken("wip")

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "project_broken"


# --- set_manually_running on a broken project -----------------------------


def test_set_manually_running_rejects_a_project_whose_published_revision_is_broken(db, project_service):
    _publish(db, project_service, "solo", VALID_YML)
    project_service.set_manually_paused("solo")
    _corrupt_published_revision(db, project_service, "solo")

    with pytest.raises(ServiceError) as exc_info:
        project_service.set_manually_running("solo")

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "project_broken"
    # Rejected before the flag is ever cleared — still manually paused.
    assert db.get_manually_paused("solo") is True


# --- get_runtime_status's own `broken` field ------------------------------


def test_get_runtime_status_reports_broken_published_and_draft_separately(db, project_service):
    _publish(db, project_service, "healthy", VALID_YML)
    _publish(db, project_service, "broken_pub", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken_pub")
    _publish(db, project_service, "broken_draft", VALID_YML)
    db.save_project_files("broken_draft", {"index.yml": BROKEN_YML.encode("utf-8")}, {"index.yml": "text/yaml"})

    rows = {row["id"]: row for row in project_service.get_runtime_status()}

    assert rows["healthy"]["broken"] == {"published": None, "draft": None}
    assert rows["broken_pub"]["broken"]["published"] is not None
    assert rows["broken_draft"]["broken"]["published"] is None
    assert rows["broken_draft"]["broken"]["draft"] is not None


def test_get_runtime_status_never_eats_a_real_transition(db, project_service):
    """A read-only runtime-status poll must never interfere with the
    transition detection recompute_availability relies on for its own
    one-notification-per-transition guarantee (see ProjectHealthChecker's
    own check() vs current() split)."""
    _publish(db, project_service, "flaky", VALID_YML)
    received = []
    subscribe(ProjectPublishedHealthChanged, received.append)

    _corrupt_published_revision(db, project_service, "flaky")
    project_service.get_runtime_status()  # polled repeatedly, e.g. by Manage projects
    project_service.get_runtime_status()
    project_service.recompute_availability("flaky")

    assert len(received) == 1
    assert received[0].project_id == "flaky" and received[0].error is not None


# --- One notification per transition --------------------------------------


def test_recompute_fires_published_health_changed_exactly_once_per_transition(db, project_service):
    _publish(db, project_service, "flaky", VALID_YML)
    received = []
    subscribe(ProjectPublishedHealthChanged, received.append)

    _corrupt_published_revision(db, project_service, "flaky")
    project_service.recompute_availability("flaky")
    project_service.recompute_availability("flaky")  # still broken — no new event
    project_service.recompute_availability("flaky")

    assert len(received) == 1
    assert received[0].error is not None


def test_recompute_fires_a_recovery_event_with_no_error(db, project_service):
    _publish(db, project_service, "flaky", VALID_YML)
    _corrupt_published_revision(db, project_service, "flaky")
    project_service.recompute_availability("flaky")
    received = []
    subscribe(ProjectPublishedHealthChanged, received.append)

    _publish(db, project_service, "flaky", VALID_YML)  # fixes it, publishes again

    assert len(received) == 1
    assert received[0].error is None


def test_recompute_all_availability_pauses_only_the_broken_project(db, project_service):
    _publish(db, project_service, "healthy", VALID_YML)
    _publish(db, project_service, "broken", VALID_YML)
    _corrupt_published_revision(db, project_service, "broken")

    project_service.recompute_all_availability()

    assert db.get_project_availability("healthy") == (False, None)
    assert db.get_project_availability("broken")[0] is True


# --- ProjectHealthNotifications: SystemWarning + ws push -------------------


def test_broken_notification_job_warns_every_admin_and_pushes_to_connected_ones(db):
    _make_admin(db, "admin1")
    _make_admin(db, "admin2")
    # "user" already exists (see conftest.py's own db fixture) with the
    # default non-admin role — must never get a warning.
    ws_adapter = FakeWsAdapter()

    job = ProjectHealthNotificationJob(db, ws_adapter, "broken", 3, "index.yml no longer builds — nope")
    job.prepare()
    asyncio.run(job.run_next_step())

    warnings_admin1 = db.get_system_warnings("admin1", "broken")
    warnings_admin2 = db.get_system_warnings("admin2", "broken")
    warnings_user = db.get_system_warnings("user", "broken")
    assert len(warnings_admin1) == 1 and warnings_admin1[0]["kind"] == "project_broken"
    assert len(warnings_admin2) == 1
    assert warnings_user == []
    pushed_usernames = {username for username, _ in ws_adapter.pushed}
    assert pushed_usernames == {"admin1", "admin2"}


def test_recovery_notification_job_warns_nobody(db):
    _make_admin(db, "admin1")
    ws_adapter = FakeWsAdapter()

    job = ProjectHealthNotificationJob(db, ws_adapter, "flaky", 4, None)
    job.prepare()
    asyncio.run(job.run_next_step())

    assert db.get_system_warnings("admin1", "flaky") == []
    assert ws_adapter.pushed == []


def test_project_health_notifications_submits_a_job_on_the_event(db):
    submitted = []

    class FakeJobService:
        def submit(self, job) -> None:
            submitted.append(job)

    notifications = ProjectHealthNotifications(db, FakeJobService(), FakeWsAdapter())
    notifications.register()

    publish(ProjectPublishedHealthChanged(project_id="broken", revision=1, error="nope"))

    assert len(submitted) == 1
    assert isinstance(submitted[0], ProjectHealthNotificationJob)


# --- Lazy recompute: a build failure discovered outside any save/publish ---


def test_a_lazy_load_failure_on_the_published_revision_pauses_the_project(db, project_service):
    """AutomatonLoader has no reference to ProjectManager — the only way
    a build failure discovered by some unrelated read (never a save/
    publish/boot sweep) reaches recompute_availability is the
    ProjectRevisionBuildFailed event (see register_availability_cascade)."""
    from automaton.build_error import AutomatonBuildError

    _publish(db, project_service, "flaky", VALID_YML)
    project_service.register_availability_cascade()
    _corrupt_published_revision(db, project_service, "flaky")
    assert db.get_project_availability("flaky") == (False, None)  # not yet noticed

    with pytest.raises(AutomatonBuildError):
        project_service.get_automaton("flaky", db.get_project_published_revision("flaky"))

    is_paused, reason = db.get_project_availability("flaky")
    assert is_paused is True
    assert "index.yml no longer builds" in reason


# --- No migration on boot: a format break just pauses the project ---------


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
    recompute_all_availability(), with nothing in between touching stored
    revisions anymore — see PROJECT_SPECS.md §8's own note): an already-
    published revision still declaring the long-removed `tools:` field is
    never rewritten, only paused, with the builder's own message surfaced
    as a project_broken SystemWarning."""
    _publish(db, project_service, "old_format", VALID_YML)
    revision = db.get_project_published_revision("old_format")
    from db.models import Archive
    Archive.update(content=TOOLS_FIELD_YML.encode("utf-8")).where(
        (Archive.project == "old_format") & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()
    project_service._manager._automaton_loader.invalidate_cache("old_format")
    before = db.get_archive("old_format", "index.yml", revision=revision)

    _make_admin(db, "admin1")
    ws_adapter = FakeWsAdapter()
    notifications = ProjectHealthNotifications(db, _SyncJobService(), ws_adapter)
    notifications.register()

    project_service.recompute_all_availability()

    after = db.get_archive("old_format", "index.yml", revision=revision)
    assert after == before  # never rewritten

    is_paused, reason = db.get_project_availability("old_format")
    assert is_paused is True
    assert "'tools' is no longer a valid field" in reason and "ai-may-read-sources" in reason

    warnings = db.get_system_warnings("admin1", "old_format")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "project_broken"
    assert "'tools' is no longer a valid field" in warnings[0]["message"]


class _SyncJobService:
    """submit() runs the job's single step inline — the boot sweep above
    has no running JobService/event loop to hand it to."""

    def submit(self, job) -> None:
        job.prepare()
        asyncio.run(job.run_next_step())
