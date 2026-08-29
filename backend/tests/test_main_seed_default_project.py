from __future__ import annotations

import pytest

from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from controller import AvanceController
from auth.auth_service import AuthService
from db import Db
from jobs import JobQueue
from main import DEFAULT_SEED_PROJECT_NAME, _seed_default_project_if_empty
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from testing.test_service import TestService
from testing.queue_progress_broadcaster import QueueProgressBroadcaster
from testing.last_status_broadcaster import LastStatusBroadcaster
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.contract


def _build(app_db: Db) -> tuple[ProjectService, AvanceController, JobQueue, QueueProgressBroadcaster]:
    fake_ai_service = FakeAiService()
    project_service = ProjectService(app_db, fake_ai_service)
    session_manager = ChatSessionManager(app_db)
    metric_service = MetricService(app_db, project_service)
    broadcaster = LastStatusBroadcaster(QueueProgressBroadcaster(fake_ai_service))
    job_queue = JobQueue(max_concurrent=1, broadcaster=broadcaster)
    tracking_service = TrackingService(app_db, project_service, metric_service)
    chat_service = ChatService(
        app_db, fake_ai_service, fake_ai_service, project_service, session_manager,
        tracking_service, metric_service, job_queue,
    )
    test_service = TestService(app_db, fake_ai_service, tracking_service, job_queue, project_service, broadcaster)
    auth_service = AuthService(app_db, [], token_ttl_in_hours=24 * 7)
    controller = AvanceController(
        chat_service, project_service, None, None, app_db, tracking_service, test_service,
        auth_service, broadcaster, job_queue, "test-version",
    )
    return project_service, controller, job_queue, broadcaster


async def test_seeds_the_default_project_when_the_database_has_none(app_db):
    project_service, controller, job_queue, _ = _build(app_db)
    assert app_db.list_projects() == []

    await _seed_default_project_if_empty(app_db, project_service, controller, job_queue)

    assert DEFAULT_SEED_PROJECT_NAME in app_db.list_projects()
    revision_info = project_service.get_project_revision_info(DEFAULT_SEED_PROJECT_NAME)
    assert revision_info["published_revision"] is not None


async def test_does_not_seed_when_a_project_already_exists(app_db, hello_project):
    project_service, controller, job_queue, _ = _build(app_db)
    assert app_db.list_projects() == ["hello"]

    await _seed_default_project_if_empty(app_db, project_service, controller, job_queue)

    assert app_db.list_projects() == ["hello"]
