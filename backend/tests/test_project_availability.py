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


def _yml_observing(project_name: str) -> str:
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
        trigger: "automaton.{project_name}.state == 'never'"
"""


def _publish_project(db, project_service: ProjectService, project_name: str, index_yml: str) -> None:
    """A real save, through _finalize_project_update, so the reverse
    index and the initial availability recompute both actually run.
    Auto-declares `project: {id: <project_name>}` when project_name is a
    valid identifier, so automaton.* references in this file resolve."""
    if project_name.isidentifier() and "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_name}\n{index_yml}"
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    db.set_active_project_name(project_name, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_automaton):
        pass

    asyncio.run(project_service._finalize_project_update(project_name, automaton, commit))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


def test_a_valid_project_with_no_dependencies_is_available(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)

    assert db.get_project_availability("solo") == (False, None)  # (is_paused, paused_reason)


def test_a_project_whose_own_saved_content_fails_to_build_is_paused(db, project_service):
    # Bypasses ProjectService's own save-time validation on purpose, since
    # every real save path already rejects a broken build outright —
    # recompute_availability must still degrade gracefully rather than raising.
    db.ensure_project("broken")
    db.save_project_files("broken", {"index.yml": b"not: [valid, yaml: at all"}, {"index.yml": "text/yaml"})
    db.publish_project("broken")

    project_service.recompute_availability("broken")

    is_paused, reason = db.get_project_availability("broken")
    assert is_paused is True
    assert "Build failed" in reason


def test_a_project_depending_on_a_paused_one_becomes_paused_too(db, project_service):
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    assert db.get_project_availability("dependent") == (False, None)

    db.set_project_availability("dependency", is_paused=True, paused_reason="manually paused")
    project_service.recompute_availability("dependent")

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dependency" in reason


def test_recompute_does_not_publish_when_nothing_actually_changed(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    received = []
    subscribe(AvailabilityChanged, received.append)

    project_service.recompute_availability("solo")  # already available, still available

    assert received == []


def test_recompute_publishes_availability_changed_when_it_flips(db, project_service):
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    received = []
    subscribe(AvailabilityChanged, received.append)

    db.set_project_availability("dependency", is_paused=True, paused_reason="manually paused")
    project_service.recompute_availability("dependent")

    assert received == [AvailabilityChanged(project_name="dependent", available=False)]


def test_pausing_a_project_cascades_through_a_dependency_chain(db, project_service):
    # a -> b -> c (a observes b, b observes c)
    _publish_project(db, project_service, "c", VALID_YML)
    _publish_project(db, project_service, "b", _yml_observing("c"))
    _publish_project(db, project_service, "a", _yml_observing("b"))
    project_service.register_availability_cascade()

    db.set_project_availability("c", is_paused=True, paused_reason="c's own build broke")
    publish(AvailabilityChanged(project_name="c", available=False))

    b_paused, b_reason = db.get_project_availability("b")
    a_paused, a_reason = db.get_project_availability("a")
    assert b_paused is True and "c" in b_reason
    assert a_paused is True and "b" in a_reason


def test_recovering_a_project_cascades_availability_back_through_the_chain(db, project_service):
    _publish_project(db, project_service, "c", VALID_YML)
    _publish_project(db, project_service, "b", _yml_observing("c"))
    _publish_project(db, project_service, "a", _yml_observing("b"))
    project_service.register_availability_cascade()
    db.set_project_availability("c", is_paused=True, paused_reason="broken")
    publish(AvailabilityChanged(project_name="c", available=False))
    assert db.get_project_availability("a")[0] is True
    assert db.get_project_availability("b")[0] is True

    # "c" itself gets fixed and re-saved — a real recompute (build
    # succeeds, no paused dependency of its own) rather than a manual
    # flip, closer to what a real recovery looks like.
    project_service.recompute_availability("c")
    publish(AvailabilityChanged(project_name="c", available=True))

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
    publish(AvailabilityChanged(project_name="a", available=False))

    assert db.get_project_availability("a")[0] is True
    assert db.get_project_availability("b")[0] is True
    # "b"'s event cascades back to "a", whose recompute finds it's
    # already paused (the "unchanged, don't republish" guard) — so
    # exactly these two events fire, total, never a third or a loop.
    assert set(received) == {
        AvailabilityChanged(project_name="a", available=False),
        AvailabilityChanged(project_name="b", available=False),
    }


def test_depending_on_a_project_that_does_not_exist_at_all_is_not_itself_blocking(db, project_service):
    """A dangling automaton.* reference is a runtime concern, not a
    reason to pause the referencing project at build time — the
    referenced project might simply not exist yet."""
    _publish_project(db, project_service, "dependent", _yml_observing("nonexistent"))

    assert db.get_project_availability("dependent") == (False, None)


# --- Manual pause/resume -----------------------------------------------


def test_set_manually_paused_only_allowed_from_running(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)

    row = project_service.set_manually_paused("solo")

    assert row == {
        "name": "solo", "status": "manually_paused", "paused_reason": "Manually paused.",
        "revision": 0, "published_revision": 0,
    }
    assert db.get_project_availability("solo") == (True, "Manually paused.")
    assert db.get_manually_paused("solo") is True


def test_set_manually_paused_rejects_a_project_that_is_already_paused(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    db.set_project_availability("solo", is_paused=True, paused_reason="Build failed: whatever")

    with pytest.raises(ValueError):
        project_service.set_manually_paused("solo")


def test_set_manually_paused_rejects_a_project_already_manually_paused(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    project_service.set_manually_paused("solo")

    with pytest.raises(ValueError):
        project_service.set_manually_paused("solo")


def test_set_manually_paused_rejects_an_unknown_project(db, project_service):
    with pytest.raises(FileNotFoundError):
        project_service.set_manually_paused("does-not-exist")


def test_set_manually_running_only_allowed_from_manually_paused(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)

    with pytest.raises(ValueError):
        project_service.set_manually_running("solo")  # currently running, nothing to resume


def test_manual_pause_then_resume_round_trips_back_to_running(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    project_service.set_manually_paused("solo")

    row = project_service.set_manually_running("solo")

    assert row["status"] == "running"
    assert db.get_project_availability("solo") == (False, None)
    assert db.get_manually_paused("solo") is False


def test_manual_pause_survives_an_unrelated_recompute(db, project_service):
    """The whole point of manually_paused (see Project.manually_paused's
    own docstring): once set, nothing but the matching resume clears it
    — not a rebuild, not a dependency flipping back and forth."""
    _publish_project(db, project_service, "solo", VALID_YML)
    project_service.set_manually_paused("solo")

    project_service.recompute_availability("solo")  # e.g. triggered by an unrelated cascade
    project_service.recompute_availability("solo")

    assert db.get_project_availability("solo") == (True, "Manually paused.")
    assert db.get_manually_paused("solo") is True


def test_manually_pausing_a_dependency_cascades_to_its_observer(db, project_service):
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    project_service.register_availability_cascade()

    project_service.set_manually_paused("dependency")

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dependency" in reason
    # The dependent was never itself manually paused — resuming it isn't
    # even a valid transition (it's 'paused', not 'manually_paused'), so
    # only resuming "dependency" itself can bring it back.
    assert db.get_manually_paused("dependent") is False


def test_resuming_a_manually_paused_dependency_cascades_availability_back(db, project_service):
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    project_service.register_availability_cascade()
    project_service.set_manually_paused("dependency")
    assert db.get_project_availability("dependent")[0] is True

    project_service.set_manually_running("dependency")

    assert db.get_project_availability("dependency") == (False, None)
    assert db.get_project_availability("dependent") == (False, None)


def test_get_runtime_status_reports_all_three_states(db, project_service):
    _publish_project(db, project_service, "running-proj", VALID_YML)
    _publish_project(db, project_service, "auto-paused-proj", VALID_YML)
    db.set_project_availability("auto-paused-proj", is_paused=True, paused_reason="Build failed: x")
    _publish_project(db, project_service, "manually-paused-proj", VALID_YML)
    project_service.set_manually_paused("manually-paused-proj")

    rows = {row["name"]: row for row in project_service.get_runtime_status()}

    assert rows["running-proj"]["status"] == "running"
    assert rows["auto-paused-proj"]["status"] == "paused"
    assert rows["manually-paused-proj"]["status"] == "manually_paused"
