"""The env a session's open/bootstrap reads and writes is the
*session's own* project's (and user's) — never the request user's
active project's.

Production bug: ChatService.env was one PersistedEnv keyed on
ProjectService.get_active_project_id() + Session().user. Opening a
session of any *other* project (the Sessions panel, a supervisor
reading someone's session, WhatsApp) made _apply_declared_env_defaults
read the active project's env to decide which declared defaults were
"missing", then write the other project's defaults into the active
project's Tracking rows — and, since the read kept answering for the
wrong project, write them again on every single open.
"""
from __future__ import annotations

import pytest

from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from chat.session_type_strategy import get_session_type_strategy
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from db.models import Tracking
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from session import Session
from tracking.env import PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

USERNAME = "user"
ACTIVE_PROJECT = "active_proj"
OTHER_PROJECT = "other_proj"


def _index_yml(project_id: str, env_key: str, default: str) -> str:
    return f"""
project:
  id: {project_id}
env:
  {env_key}:
    value: "'{default}'"
init-action:
  target: a
states:
  a:
    ui-label: a
    contextual-prompt: hi
"""


def _publish(db, project_id: str, yml: str) -> None:
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)


def _chat_service(db, project_service: ProjectService) -> ChatService:
    ai_service = FakeAiService()
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service,
        ai_test_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=ChatSessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        job_service=job_service,
        actuator_factory=actuator_factory,
    )


def _env(db, project_id: str, username: str = USERNAME) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=project_id), username=username)


@pytest.fixture
def two_projects(db) -> tuple[ProjectService, ChatService]:
    _publish(db, ACTIVE_PROJECT, _index_yml(ACTIVE_PROJECT, "active_key", "active-default"))
    _publish(db, OTHER_PROJECT, _index_yml(OTHER_PROJECT, "other_key", "other-default"))
    db.set_active_project_id(ACTIVE_PROJECT, USERNAME)
    project_service = ProjectService(db)
    return project_service, _chat_service(db, project_service)


async def test_opening_another_projects_session_writes_that_projects_env_not_the_active_ones(db, two_projects):
    project_service, chat_service = two_projects
    # The active project's own session, bootstrapped the normal way.
    active_session = await chat_service.get_current_session_if_any_or_create_new(None)
    await chat_service.open_if_needed(active_session["id"])
    assert _env(db, ACTIVE_PROJECT).action_set() == {"active_key": "active-default"}

    # A session of the *other* project (still ACTIVE_PROJECT active) —
    # what the Sessions panel or WhatsApp does.
    other_session_id = chat_service._session_manager.create_session(
        get_session_type_strategy("live"), project_service, USERNAME, OTHER_PROJECT
    )["id"]
    await chat_service.open_if_needed(other_session_id)

    assert _env(db, OTHER_PROJECT).action_set() == {"other_key": "other-default"}
    assert _env(db, ACTIVE_PROJECT).action_set() == {"active_key": "active-default"}


async def test_reopening_another_projects_session_is_a_no_op_once_its_defaults_are_set(db, two_projects):
    project_service, chat_service = two_projects
    other_session_id = chat_service._session_manager.create_session(
        get_session_type_strategy("live"), project_service, USERNAME, OTHER_PROJECT
    )["id"]

    for _ in range(3):
        await chat_service.open_if_needed(other_session_id)

    assert _env(db, OTHER_PROJECT).action_set() == {"other_key": "other-default"}
    assert db.get_action_env(ACTIVE_PROJECT, USERNAME) == {}
    # One write, on the first open — never one per open.
    action_env_rows = Tracking.select().where(
        (Tracking.session == other_session_id) & Tracking.action_env.is_null(False)
    ).count()
    assert action_env_rows == 1


async def test_a_supervisor_opening_someone_elses_session_touches_that_users_env(db, two_projects):
    project_service, chat_service = two_projects
    db.get_or_create_user("test", "sub-alice", "alice", "alice", None)
    db.set_active_project_id(ACTIVE_PROJECT, "alice")
    alice_session_id = chat_service._session_manager.create_session(
        get_session_type_strategy("live"), project_service, "alice", ACTIVE_PROJECT
    )["id"]

    # Default fixture identity is "user" with role supervisor. Only the
    # bootstrap half of open_if_needed: the opening-message half is a
    # real turn, which a supervisor rightly can't run on alice's session.
    assert Session().user == USERNAME
    await chat_service._ensure_project_bootstrap(alice_session_id)

    assert _env(db, ACTIVE_PROJECT, username="alice").action_set() == {"active_key": "active-default"}
    assert db.get_action_env(ACTIVE_PROJECT, USERNAME) == {}
