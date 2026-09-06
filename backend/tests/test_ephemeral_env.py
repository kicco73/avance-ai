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


async def test_a_test_sessions_env_lives_only_in_memory_isolated_from_the_live_one_and_from_the_next_test_session(service):
    live_session = await service.get_current_session_if_any_or_create_new(None)
    first = await service.create_draft_session(PROJECT_ID)

    result = await service.apply_manual_action("advance", first["id"])

    assert result["state"]["key"] == "b"
    assert _env_tracking_row_count(first["id"]) == 0
    assert service.get_env(first["id"])["action_set"] == {"favorite_color": "blue"}
    assert service.get_env(live_session["id"])["action_set"] == {}

    second = await service.create_draft_session(PROJECT_ID)
    assert service.get_env(second["id"])["action_set"] == {}


@pytest.mark.parametrize("discard", ["reset", "delete", "close"])
async def test_resetting_deleting_or_closing_a_test_session_discards_its_ephemeral_env(service, discard):
    session = await service.create_draft_session(PROJECT_ID)
    await service.apply_manual_action("advance", session["id"])
    assert EphemeralEnvRegistry().get(session["id"]).action_set() == {"favorite_color": "blue"}

    if discard == "reset":
        service.reset_test_sessions(PROJECT_ID)
    elif discard == "delete":
        service.delete_session(session["id"])
    else:
        await service.close_session(session["id"])

    assert EphemeralEnvRegistry().get(session["id"]).action_set() == {}
