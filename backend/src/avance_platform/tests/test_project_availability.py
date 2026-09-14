"""Project availability: a project is available when its own build
succeeds and nobody paused it by hand. is_paused/paused_reason only change
when the recomputed value actually differs.
"""
from __future__ import annotations

import asyncio

import pytest

from avance_platform.platform_service import PlatformService

from automaton.automaton_builder import AutomatonBuilder
from turn.sessions.session_manager import SessionManager
from project.archive.automaton_loader import AutomatonLoader
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


def _publish_project(db, project_service: ProjectService, project_id: str, index_yml: str) -> None:
    """A real save, through finalize_update, so the initial availability
    recompute actually runs. Auto-declares `project: {id: <project_id>}`
    when index_yml doesn't already declare its own."""
    if "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_id}\n{index_yml}"
    is_new_project = not db.project_exists(project_id)
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)
    db.set_active_project_id(project_id, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})
    asyncio.run(project_service.manager.finalize_update(project_id, automaton, is_new_project=is_new_project))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db, AutomatonLoader(db), SessionManager(db))


def test_a_valid_project_with_no_dependencies_is_available_while_one_whose_saved_content_fails_to_build_is_paused(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    assert db.get_project_availability("solo") == (False, None)
    db.ensure_project("broken")
    db.save_project_files("broken", {"index.yml": b"not: [valid, yaml: at all"}, {"index.yml": "text/yaml"})
    db.publish_project("broken")
    project_service.recompute_availability("broken")
    is_paused, reason = db.get_project_availability("broken")
    assert is_paused is True
    assert "index.yml no longer builds" in reason



def test_manual_pause_and_resume_are_the_only_transitions_between_running_and_manually_paused_and_survive_recomputes(db, project_service):
    """The whole point of manually_paused (see Project.manually_paused's
    own docstring): once set, nothing but the matching resume clears it
    — not a rebuild, not a dependency flipping back and forth."""
    with pytest.raises(FileNotFoundError):
        PlatformService(project_service).set_manually_paused("does_not_exist")

    _publish_project(db, project_service, "solo", VALID_YML)
    with pytest.raises(ValueError):
        PlatformService(project_service).set_manually_running("solo")

    row = PlatformService(project_service).set_manually_paused("solo")
    assert row == {
        "id": "solo", "status": "manually_paused", "paused_reason": "Manually paused.",
        "revision": 0, "published_revision": 0,
        "broken": {"published": None, "draft": None},
    }
    assert db.get_project_availability("solo") == (True, "Manually paused.")
    assert db.get_manually_paused("solo") is True
    with pytest.raises(ValueError):
        PlatformService(project_service).set_manually_paused("solo")

    project_service.recompute_availability("solo")
    project_service.recompute_availability("solo")
    assert db.get_project_availability("solo") == (True, "Manually paused.")
    assert db.get_manually_paused("solo") is True

    row = PlatformService(project_service).set_manually_running("solo")
    assert row["status"] == "running"
    assert db.get_project_availability("solo") == (False, None)
    assert db.get_manually_paused("solo") is False

    db.set_project_availability("solo", is_paused=True, paused_reason="Build failed: whatever")
    with pytest.raises(ValueError):
        PlatformService(project_service).set_manually_paused("solo")


def test_get_runtime_status_reports_all_three_states(db, project_service):
    _publish_project(db, project_service, "running_proj", VALID_YML)
    _publish_project(db, project_service, "auto_paused_proj", VALID_YML)
    db.set_project_availability("auto_paused_proj", is_paused=True, paused_reason="Build failed: x")
    _publish_project(db, project_service, "manually_paused_proj", VALID_YML)
    PlatformService(project_service).set_manually_paused("manually_paused_proj")

    rows = {row["id"]: row for row in PlatformService(project_service).get_runtime_status()}

    assert rows["running_proj"]["status"] == "running"
    assert rows["auto_paused_proj"]["status"] == "paused"
    assert rows["manually_paused_proj"]["status"] == "manually_paused"
