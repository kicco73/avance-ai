"""turn.sessions.session_type_strategy.PreviewSessionStrategy.discard_superseded."""
from __future__ import annotations

import pytest

from conftest import FakeAiService, make_test_namespace_factory, make_test_scheduler_service
from db.models import CoreSession
from metrics.metric_service import MetricService
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService
from turn.ephemeral_env_registry import EphemeralEnvRegistry
from turn.sessions.session_manager import SessionManager
from turn.turn_service import TurnService

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "preview_proj"
OTHER_PROJECT_ID = "preview_other"

_INDEX_YML = """
project:
  id: {id}
init-action:
  target: a
states:
  a:
    ui-label: A
    input-processor: ai
    contextual-prompt: hi
"""


def _publish(db, project_id: str) -> None:
    db.ensure_project(project_id)
    db.save_project_files(
        project_id, {"index.yml": _INDEX_YML.format(id=project_id).encode("utf-8")}, {"index.yml": "text/yaml"},
    )
    db.publish_project(project_id)


def _turn_service(db) -> TurnService:
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    ai_service = FakeAiService()
    metric_service = MetricService(db, project_service)
    scheduler_service = make_test_scheduler_service(db)
    namespace_factory = make_test_namespace_factory(db, scheduler_service)
    tracking_service = TrackingService(db, project_service, metric_service, namespace_factory)
    return TurnService(
        ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
        session_manager=SessionManager(db), tracking_service=tracking_service, metric_service=metric_service,
        scheduler_service=scheduler_service, namespace_factory=namespace_factory,
    )


def _session_ids(project_id: str, type: str) -> list[int]:
    return sorted(
        row.id for row in CoreSession.select().where(
            (CoreSession.username == USERNAME) & (CoreSession.project == project_id) & (CoreSession.type == type)
        )
    )


@pytest.fixture
def service(db) -> TurnService:
    _publish(db, PROJECT_ID)
    _publish(db, OTHER_PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    return _turn_service(db)


async def test_a_new_preview_leaves_no_earlier_preview_row_of_that_person_behind(service):
    """Every road to a preview goes through SessionManager.create_session,
    so this holds for the Bus's `session.create` as much as for the older
    route that used to do the deleting itself."""
    first = await service.create_session_of(PROJECT_ID, 'preview')
    EphemeralEnvRegistry().get(first["id"]).update_action_set({"colour": "blue"})

    second = await service.create_session_of(PROJECT_ID, 'preview')

    assert _session_ids(PROJECT_ID, 'preview') == [second["id"]]
    assert EphemeralEnvRegistry().get(first["id"]).action_set() == {}


async def test_previewing_another_project_supersedes_the_preview_of_the_first(service):
    await service.create_session_of(PROJECT_ID, 'preview')

    second = await service.create_session_of(OTHER_PROJECT_ID, 'preview')

    assert _session_ids(PROJECT_ID, 'preview') == []
    assert _session_ids(OTHER_PROJECT_ID, 'preview') == [second["id"]]


async def test_a_live_session_keeps_the_previous_ones(service):
    first = await service.create_session_of(PROJECT_ID, 'live')

    second = await service.create_session_of(PROJECT_ID, 'live')

    assert _session_ids(PROJECT_ID, 'live') == sorted([first["id"], second["id"]])
