"""The env a session's open/bootstrap reads and writes is the
*session's own* project's (and user's) — never the request user's
active project's.

Production bug: TurnService.env was one PersistedEnv keyed on
ProjectService.get_active_project_id() + WebSession().user. Opening a
session of any *other* project (the Sessions panel, a supervisor
reading someone's session, WhatsApp) made _backfill_declared_env_keys
read the active project's env to decide which declared defaults were
"missing", then write the other project's defaults into the active
project's Tracking rows — and, since the read kept answering for the
wrong project, write them again on every single open.
"""
from __future__ import annotations

import pytest

from turn.turn_service import TurnService
from turn.sessions.session_manager import SessionManager
from conftest import FakeAiService, make_test_namespace_factory, make_test_scheduler_service
from db.models import Tracking
from metrics.metric_service import MetricService
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from system.web_session import WebSession
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
    type: string
init-action:
  target: a
  env:
    {env_key}: "'{default}'"
states:
  a:
    ui-label: a
    contextual-prompt: hi
    actions:
      - name: advance
        target: a
"""


def _publish(db, project_id: str, yml: str) -> None:
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)


def _turn_service(db, project_service: ProjectService) -> TurnService:
    ai_service = FakeAiService()
    metric_service = MetricService(db, project_service)
    scheduler_service = make_test_scheduler_service(db)
    namespace_factory = make_test_namespace_factory(db, scheduler_service)
    tracking_service = TrackingService(db, project_service, metric_service, namespace_factory)
    return TurnService(
        ai_service=ai_service,
        ai_test_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=SessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        scheduler_service=scheduler_service,
        namespace_factory=namespace_factory,
    )


def _env(db, project_id: str, username: str = USERNAME) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=project_id), session_id=0, username=username)


@pytest.fixture
def two_projects(db) -> TurnService:
    _publish(db, ACTIVE_PROJECT, _index_yml(ACTIVE_PROJECT, "active_key", "active-default"))
    _publish(db, OTHER_PROJECT, _index_yml(OTHER_PROJECT, "other_key", "other-default"))
    db.set_active_project_id(ACTIVE_PROJECT, USERNAME)
    return _turn_service(db, ProjectService(db, AutomatonLoader(db), SessionManager(db)))


async def test_opening_another_projects_session_writes_that_projects_env_not_the_active_ones(db, two_projects):
    turn_service = two_projects
    active_session = await turn_service.enter_session(ACTIVE_PROJECT, 'live')
    await turn_service.open_conversation(active_session["id"])
    assert _env(db, ACTIVE_PROJECT).action_set() == {"active_key": "active-default"}
    other_session_id = (await turn_service.enter_session(OTHER_PROJECT, 'live'))["id"]
    await turn_service.open_conversation(other_session_id)

    assert _env(db, OTHER_PROJECT).action_set() == {"other_key": "other-default"}
    assert _env(db, ACTIVE_PROJECT).action_set() == {"active_key": "active-default"}


async def test_another_projects_defaults_are_written_once_however_often_it_is_opened(db, two_projects):
    turn_service = two_projects
    other_session_id = (await turn_service.enter_session(OTHER_PROJECT, 'live'))["id"]

    for _ in range(3):
        await turn_service.open_conversation(other_session_id)

    assert _env(db, OTHER_PROJECT).action_set() == {"other_key": "other-default"}
    assert db.get_action_env(ACTIVE_PROJECT, USERNAME) == {}
    action_env_rows = Tracking.select().where(
        (Tracking.session == other_session_id) & Tracking.action_env.is_null(False)
    ).count()
    assert action_env_rows == 2


async def test_a_supervisor_opening_someone_elses_session_touches_that_users_env(db, two_projects):
    turn_service = two_projects
    db.get_or_create_user("test", "sub-alice", "alice", "alice", None)
    db.set_active_project_id(ACTIVE_PROJECT, "alice")
    with WebSession().impersonate("alice"):
        alice_session_id = (await turn_service.enter_session(ACTIVE_PROJECT, 'live'))["id"]
    assert WebSession().user == USERNAME
    assert await turn_service.prepare_user_initiated_turn(alice_session_id) == []

    assert _env(db, ACTIVE_PROJECT, username="alice").action_set() == {"active_key": "active-default"}
    assert db.get_action_env(ACTIVE_PROJECT, USERNAME) == {}
