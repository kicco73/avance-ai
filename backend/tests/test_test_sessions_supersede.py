"""turn.sessions.session_type_strategy.TestSessionStrategy.discard_superseded."""
from __future__ import annotations

import asyncio

import pytest

from conftest import FakeAiService, RecordedMessages, make_test_namespace_factory, make_test_scheduler_service
from db.models import CoreSession
from metrics.metric_service import MetricService
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from system.bus import OUTPUT_DRIVE
from tracking.tracking_service import TrackingService
from turn.sessions.session_manager import SessionManager
from turn.turn_service import TurnService

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "test_proj"
OTHER_PROJECT_ID = "test_other"

_INDEX_YML = """
project:
  id: {id}
init-action:
  target: a
states:
  a:
    ui-label: A
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


async def test_a_new_test_session_clears_the_previous_test_sessions_drive_but_keeps_its_row(service, db):
    first = await service.create_session_of(PROJECT_ID, 'test')
    db.write_drive_file(PROJECT_ID, USERNAME, "reports/last.md", b"ciao", "text/markdown", first["id"])

    second = await service.create_session_of(PROJECT_ID, 'test')

    assert db.read_drive_file(PROJECT_ID, USERNAME, "reports/last.md") is None
    assert _session_ids(PROJECT_ID, 'test') == sorted([first["id"], second["id"]])


async def test_a_new_test_session_leaves_another_projects_test_drive_alone(service, db):
    first = await service.create_session_of(PROJECT_ID, 'test')
    db.write_drive_file(PROJECT_ID, USERNAME, "reports/last.md", b"ciao", "text/markdown", first["id"])
    other = await service.create_session_of(OTHER_PROJECT_ID, 'test')
    db.write_drive_file(OTHER_PROJECT_ID, USERNAME, "reports/last.md", b"altro", "text/markdown", other["id"])

    await service.create_session_of(PROJECT_ID, 'test')

    assert db.read_drive_file(OTHER_PROJECT_ID, USERNAME, "reports/last.md")[0] == b"altro"


async def test_a_new_test_session_publishes_output_drive_only_when_it_actually_clears_something(service, db):
    recorded = RecordedMessages(OUTPUT_DRIVE)
    await service.create_session_of(PROJECT_ID, 'test')
    assert recorded.of_type(OUTPUT_DRIVE) == []

    first = await service.create_session_of(PROJECT_ID, 'test')
    db.write_drive_file(PROJECT_ID, USERNAME, "reports/last.md", b"ciao", "text/markdown", first["id"])
    recorded = RecordedMessages(OUTPUT_DRIVE)

    await service.create_session_of(PROJECT_ID, 'test')
    await asyncio.sleep(0)

    (message,) = recorded.of_type(OUTPUT_DRIVE)
    assert message.username == USERNAME
    assert message.project_id == PROJECT_ID
    assert message.body == {"path": None}
