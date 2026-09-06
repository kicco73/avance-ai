"""Cross-project availability: a project is available when its own build
succeeds and every project it references via automaton.* is itself
available. is_paused/paused_reason only change when the recomputed value
actually differs, letting a cascade converge safely even across a mutual
dependency, with no cycle detection of its own.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton_builder import AutomatonBuilder
from events import AvailabilityChanged, publish, subscribe
from project.project_service import ProjectService

pytestmark = pytest.mark.contract

USERNAME = "user"

VALID_YML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def _yml_observing(project_id: str) -> str:
    return f"""
init-action:
  target: x
states:
  x:
    ui-label: X
    contextual-prompt: hi
    actions:
      - name: notice
        target: x
        trigger: "automaton.{project_id}.state == 'never'"
"""


async def _commit(_project_id, _automaton):
    pass


def _publish_project(db, project_service: ProjectService, project_id: str, index_yml: str) -> None:
    """A real save, through finalize_update, so the reverse index and the
    initial availability recompute both actually run. Auto-declares
    `project: {id: <project_id>, family: test}` when index_yml doesn't
    already declare its own — every project in this file shares family
    "test" so automaton.* references resolve; family only gates that
    (build-time knowledge + runtime automaton.<id>.state/env reads), never
    the plain existence-based availability cascade this file actually
    exercises."""
    if "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_id}\n  family: test\n{index_yml}"
    is_new_project = not db.project_exists(project_id)
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)
    db.set_active_project_id(project_id, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})
    asyncio.run(project_service._manager.finalize_update(project_id, automaton, _commit, is_new_project=is_new_project))


def _chain(db, project_service) -> None:
    # a -> b -> c (a observes b, b observes c)
    _publish_project(db, project_service, "c", VALID_YML)
    _publish_project(db, project_service, "b", _yml_observing("c"))
    _publish_project(db, project_service, "a", _yml_observing("b"))
    project_service.register_availability_cascade()


def _dependency_pair(db, project_service, cascade: bool = False) -> None:
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    assert db.get_project_availability("dependent") == (False, None)
    if cascade:
        project_service.register_availability_cascade()


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


def test_a_valid_project_with_no_dependencies_is_available_while_one_whose_saved_content_fails_to_build_is_paused(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    assert db.get_project_availability("solo") == (False, None)

    # Bypasses ProjectService's own save-time validation on purpose, since
    # every real save path already rejects a broken build outright —
    # recompute_availability must still degrade gracefully rather than raising.
    db.ensure_project("broken")
    db.save_project_files("broken", {"index.yml": b"not: [valid, yaml: at all"}, {"index.yml": "text/yaml"})
    db.publish_project("broken")
    project_service.recompute_availability("broken")
    is_paused, reason = db.get_project_availability("broken")
    assert is_paused is True
    assert "index.yml no longer builds" in reason


def test_a_project_depending_on_a_paused_one_becomes_paused_and_recompute_publishes_only_when_availability_flips(db, project_service):
    _dependency_pair(db, project_service)
    received = []
    subscribe(AvailabilityChanged, received.append)

    project_service.recompute_availability("dependent")
    assert received == []

    db.set_project_availability("dependency", is_paused=True, paused_reason="manually paused")
    project_service.recompute_availability("dependent")

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dependency" in reason
    assert received == [AvailabilityChanged(project_id="dependent", available=False)]


def test_pausing_and_recovering_a_project_cascade_through_a_dependency_chain(db, project_service):
    _chain(db, project_service)

    db.set_project_availability("c", is_paused=True, paused_reason="c's own build broke")
    publish(AvailabilityChanged(project_id="c", available=False))

    b_paused, b_reason = db.get_project_availability("b")
    a_paused, a_reason = db.get_project_availability("a")
    assert b_paused is True and "c" in b_reason
    assert a_paused is True and "b" in a_reason

    # "c" itself gets fixed and re-saved — a real recompute (build
    # succeeds, no paused dependency of its own) rather than a manual
    # flip, closer to what a real recovery looks like.
    project_service.recompute_availability("c")
    publish(AvailabilityChanged(project_id="c", available=True))

    assert db.get_project_availability("c") == (False, None)
    assert db.get_project_availability("b") == (False, None)
    assert db.get_project_availability("a") == (False, None)


def test_a_mutual_dependency_between_two_projects_converges_without_looping_forever(db, project_service):
    # a observes b AND b observes a — a genuine cycle. This test's own
    # completion (no RecursionError/hang) is half the assertion; the
    # other half is that both ends up paused exactly once.
    _publish_project(db, project_service, "a", _yml_observing("b"))
    _publish_project(db, project_service, "b", _yml_observing("a"))
    project_service.register_availability_cascade()
    received = []
    subscribe(AvailabilityChanged, received.append)

    db.set_project_availability("a", is_paused=True, paused_reason="a's own build broke")
    publish(AvailabilityChanged(project_id="a", available=False))

    assert db.get_project_availability("a")[0] is True
    assert db.get_project_availability("b")[0] is True
    # "b"'s event cascades back to "a", whose recompute finds it's
    # already paused (the "unchanged, don't republish" guard) — so
    # exactly these two events fire, total, never a third or a loop.
    assert set(received) == {
        AvailabilityChanged(project_id="a", available=False),
        AvailabilityChanged(project_id="b", available=False),
    }


def test_a_dangling_reference_never_blocks_but_the_referenced_projects_arrival_wakes_the_dependent_up(db, project_service):
    """A dangling automaton.* reference is a runtime concern, not a reason
    to pause the referencing project at build time — the referenced
    project might simply not exist yet. Once it is actually created, the
    dependent must pick up the dependency on its own — nothing ever
    re-saves it."""
    _publish_project(db, project_service, "dependent", _yml_observing("dep"))
    project_service.register_availability_cascade()
    assert db.get_project_availability("dependent") == (False, None)
    assert db.get_observed_projects("dependent") == []

    _publish_project(db, project_service, "dep", VALID_YML)
    assert db.get_observed_projects("dependent") == ["dep"]

    project_service.set_manually_paused("dep")

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dep" in reason


def test_changing_a_projects_id_pauses_observers_of_the_stale_old_id(db, project_service):
    """"dependent" observes "old_id". Renaming that same project's id out
    from under it (a real re-save, addressed by its own *current* id —
    project.id is this project's one and only identity now, so "renaming"
    it really is renaming the project) must pause "dependent" immediately
    — without "dependent" itself being touched."""
    _publish_project(db, project_service, "old_id", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("old_id"))
    assert db.get_project_availability("dependent") == (False, None)

    _publish_project(db, project_service, "old_id", "project:\n  id: new_id\n" + VALID_YML)

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "old_id" in reason


# --- Manual pause/resume -----------------------------------------------


def test_manual_pause_and_resume_are_the_only_transitions_between_running_and_manually_paused_and_survive_recomputes(db, project_service):
    """The whole point of manually_paused (see Project.manually_paused's
    own docstring): once set, nothing but the matching resume clears it
    — not a rebuild, not a dependency flipping back and forth."""
    with pytest.raises(FileNotFoundError):
        project_service.set_manually_paused("does_not_exist")

    _publish_project(db, project_service, "solo", VALID_YML)
    with pytest.raises(ValueError):
        project_service.set_manually_running("solo")

    row = project_service.set_manually_paused("solo")
    assert row == {
        "id": "solo", "status": "manually_paused", "paused_reason": "Manually paused.",
        "revision": 0, "published_revision": 0,
    }
    assert db.get_project_availability("solo") == (True, "Manually paused.")
    assert db.get_manually_paused("solo") is True
    with pytest.raises(ValueError):
        project_service.set_manually_paused("solo")

    project_service.recompute_availability("solo")
    project_service.recompute_availability("solo")
    assert db.get_project_availability("solo") == (True, "Manually paused.")
    assert db.get_manually_paused("solo") is True

    row = project_service.set_manually_running("solo")
    assert row["status"] == "running"
    assert db.get_project_availability("solo") == (False, None)
    assert db.get_manually_paused("solo") is False

    db.set_project_availability("solo", is_paused=True, paused_reason="Build failed: whatever")
    with pytest.raises(ValueError):
        project_service.set_manually_paused("solo")


def test_manually_pausing_a_dependency_cascades_to_its_observer_and_resuming_it_cascades_availability_back(db, project_service):
    _dependency_pair(db, project_service, cascade=True)

    project_service.set_manually_paused("dependency")

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dependency" in reason
    # The dependent was never itself manually paused — resuming it isn't
    # even a valid transition (it's 'paused', not 'manually_paused'), so
    # only resuming "dependency" itself can bring it back.
    assert db.get_manually_paused("dependent") is False

    project_service.set_manually_running("dependency")

    assert db.get_project_availability("dependency") == (False, None)
    assert db.get_project_availability("dependent") == (False, None)


def test_deleting_a_project_pauses_its_observer(db, project_service):
    """A dependency that was resolved once (and recorded in the observer
    index) and later deleted must block its observer — unlike a reference
    that never resolved to a real project in the first place."""
    _dependency_pair(db, project_service)

    asyncio.run(project_service.delete_project("dependency", _commit))

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dependency" in reason


def test_get_runtime_status_reports_all_three_states(db, project_service):
    _publish_project(db, project_service, "running_proj", VALID_YML)
    _publish_project(db, project_service, "auto_paused_proj", VALID_YML)
    db.set_project_availability("auto_paused_proj", is_paused=True, paused_reason="Build failed: x")
    _publish_project(db, project_service, "manually_paused_proj", VALID_YML)
    project_service.set_manually_paused("manually_paused_proj")

    rows = {row["id"]: row for row in project_service.get_runtime_status()}

    assert rows["running_proj"]["status"] == "running"
    assert rows["auto_paused_proj"]["status"] == "paused"
    assert rows["manually_paused_proj"]["status"] == "manually_paused"
