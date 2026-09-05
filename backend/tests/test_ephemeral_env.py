"""chat.env_for_session / chat.ephemeral_env_registry."""
from __future__ import annotations

import pytest

from chat.chat_service import ChatService
from chat.ephemeral_env_registry import EphemeralEnvRegistry
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from db.models import Tracking
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "eph_proj"

_INDEX_YML = f"""
project:
  id: {PROJECT_ID}
env:
  favorite_color:
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: advance
        target: b
        env:
          favorite_color: "'blue'"
  b:
    ui-label: B
    contextual-prompt: bye
"""


def _publish(db) -> None:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": _INDEX_YML.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)


def _chat_service(db, project_service: ProjectService) -> ChatService:
    ai_service = FakeAiService()
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
        session_manager=ChatSessionManager(db), tracking_service=tracking_service, metric_service=metric_service,
        job_service=job_service, actuator_factory=actuator_factory,
    )


@pytest.fixture
def service(db) -> ChatService:
    _publish(db)
    return _chat_service(db, ProjectService(db))


def _env_tracking_row_count(session_id: int) -> int:
    return Tracking.select().where(
        (Tracking.session == session_id) & (Tracking.env.is_null(False) | Tracking.action_env.is_null(False))
    ).count()


async def test_a_test_sessions_action_env_write_leaves_no_tracking_row(service):
    session = await service.create_draft_session(PROJECT_ID)

    result = await service.apply_manual_action("advance", session["id"])

    assert result["state"]["key"] == "b"
    assert _env_tracking_row_count(session["id"]) == 0
    assert service.get_env(session["id"])["action_set"] == {"favorite_color": "blue"}


async def test_the_live_env_is_untouched_by_a_test_sessions_turn(service):
    live_session = await service.get_current_session_if_any_or_create_new(None)
    test_session = await service.create_draft_session(PROJECT_ID)

    await service.apply_manual_action("advance", test_session["id"])

    assert service.get_env(live_session["id"])["action_set"] == {}


async def test_two_consecutive_test_sessions_of_the_same_user_do_not_see_each_others_env(service):
    first = await service.create_draft_session(PROJECT_ID)
    await service.apply_manual_action("advance", first["id"])

    second = await service.create_draft_session(PROJECT_ID)

    assert service.get_env(second["id"])["action_set"] == {}


async def test_reset_test_sessions_discards_its_ephemeral_env(service):
    session = await service.create_draft_session(PROJECT_ID)
    await service.apply_manual_action("advance", session["id"])
    assert EphemeralEnvRegistry().get(session["id"]).action_set() == {"favorite_color": "blue"}

    service.reset_test_sessions(PROJECT_ID)

    assert EphemeralEnvRegistry().get(session["id"]).action_set() == {}


async def test_delete_session_discards_its_ephemeral_env(service):
    session = await service.create_draft_session(PROJECT_ID)
    await service.apply_manual_action("advance", session["id"])

    service.delete_session(session["id"])

    assert EphemeralEnvRegistry().get(session["id"]).action_set() == {}


async def test_close_session_discards_its_ephemeral_env(service):
    session = await service.create_draft_session(PROJECT_ID)
    await service.apply_manual_action("advance", session["id"])

    await service.close_session(session["id"])

    assert EphemeralEnvRegistry().get(session["id"]).action_set() == {}
