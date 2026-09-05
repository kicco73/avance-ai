"""ChatService._cleanup_orphan_action_env_keys."""
from __future__ import annotations

from datetime import datetime

import pytest

from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from chat.session_type_strategy import get_session_type_strategy
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from db.models import Tracking
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "orphan_proj"


def _publish(db, env_keys: list[str]) -> None:
    env_section = "\n".join(f"  {key}:" for key in env_keys)
    yml = f"""
project:
  id: {PROJECT_ID}
env:
{env_section}
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)


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


def _seed_older_session_with_action_env(db, values: dict) -> int:
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2020, 1, 1), datetime_end=datetime(2020, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_action_env(session_id, values)
    return session_id


async def test_a_new_live_sessions_bootstrap_drops_a_key_its_own_revision_no_longer_declares(db):
    _publish(db, ["old_key", "keep_key"])
    _seed_older_session_with_action_env(db, {"old_key": 1, "keep_key": 2})
    _publish(db, ["keep_key"])
    project_service = ProjectService(db)
    chat_service = _chat_service(db, project_service)
    new_session_id = chat_service._session_manager.create_session(
        get_session_type_strategy("live"), project_service, USERNAME, PROJECT_ID
    )["id"]

    await chat_service.open_if_needed(new_session_id)

    assert chat_service.get_env(new_session_id)["action_set"] == {"keep_key": 2}


async def test_the_cleanup_only_writes_once_not_on_every_later_open(db):
    _publish(db, ["old_key", "keep_key"])
    _seed_older_session_with_action_env(db, {"old_key": 1, "keep_key": 2})
    _publish(db, ["keep_key"])
    project_service = ProjectService(db)
    chat_service = _chat_service(db, project_service)
    new_session_id = chat_service._session_manager.create_session(
        get_session_type_strategy("live"), project_service, USERNAME, PROJECT_ID
    )["id"]

    for _ in range(3):
        await chat_service.open_if_needed(new_session_id)

    action_env_rows = Tracking.select().where(Tracking.action_env.is_null(False)).count()
    assert action_env_rows == 2


async def test_no_cleanup_write_when_nothing_is_orphaned(db):
    _publish(db, ["keep_key"])
    _seed_older_session_with_action_env(db, {"keep_key": 2})
    project_service = ProjectService(db)
    chat_service = _chat_service(db, project_service)
    new_session_id = chat_service._session_manager.create_session(
        get_session_type_strategy("live"), project_service, USERNAME, PROJECT_ID
    )["id"]

    await chat_service.open_if_needed(new_session_id)

    action_env_rows = Tracking.select().where(Tracking.action_env.is_null(False)).count()
    assert action_env_rows == 1
    assert chat_service.get_env(new_session_id)["action_set"] == {"keep_key": 2}
