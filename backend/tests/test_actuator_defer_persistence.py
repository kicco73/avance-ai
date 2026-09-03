"""actuator.defer across a restart, end to end: an on-enter lambda is
hibernated as source + frozen scope under (user, project, revision),
and a brand-new JobService/factory over the same database runs it
against an equivalent environment — the frozen part (user/signal/env)
exactly as the lambda would have seen it, the live part (actuator.*,
metric.*, ...) rebuilt for that user and that project revision — with
no session anywhere in sight.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from automaton.automaton_builder import AutomatonBuilder
from chat.ws_adapter import WsAdapter
from conftest import make_test_actuator_factory, make_test_job_service
from db import Db
from db.models import Task as TaskRow, User
from job import JobService
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.actuators.deferred_task import DeferredActuatorTask
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT = "reminders"

INDEX_YML = """
project:
  id: reminders
  ui-label: Reminders
init-action:
  target: a
signals:
  distress:
    definition: how much distress the user reports
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          actuator.defer(lambda: actuator.notify(user.name, 'high' if signal.distress > 50 else 'low'), datetime.datetime(2030, 1, 1))
  b:
    ui-label: B
    contextual-prompt: there
"""


def _wait_until(predicate, timeout=3.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict):
        self.sent.append(payload)


_live_services: list[JobService] = []


@pytest.fixture(autouse=True)
def _stop_services():
    yield
    while _live_services:
        _live_services.pop().stop()


@pytest.fixture
def file_db(tmp_path) -> Db:
    # File-backed: the scheduler/queue threads open their own connections.
    instance = Db(f"sqlite:///{tmp_path / 'defer.db'}")
    instance.get_or_create_user("test", "sub-user", "user", "Ada", None)
    return instance


def _publish(db: Db, project_service: ProjectService, index_yml: str = INDEX_YML) -> None:
    db.ensure_project(PROJECT)
    db.save_project_files(PROJECT, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT)
    db.set_active_project_id(PROJECT, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_project_id, _automaton):
        pass

    asyncio.run(project_service._manager.finalize_update(PROJECT, automaton, commit))


def _process(db: Db, websocket: _FakeWebSocket | None = None, *, start: bool = False):
    """One "process": a JobService, a ProjectService and an actuator
    factory over `db`, wired the way main.py does — started only when
    asked, since a not-yet-started service is exactly what a process
    still wiring itself up looks like."""
    job_service = make_test_job_service(db)
    _live_services.append(job_service)
    project_service = ProjectService(db)
    factory = make_test_actuator_factory(db, job_service, project_service)
    if websocket is not None:
        ws_adapter = WsAdapter(chat_service=None, db=db, auth_service=None)
        ws_adapter._connections[USERNAME] = websocket
        factory.set_ws_adapter(ws_adapter)
    if start:
        job_service.start()
    return job_service, project_service, factory


def _defer_through_on_enter(db: Db, factory, project_service: ProjectService, signal_values: dict) -> str | None:
    """Renders state a's `go` on-enter the way TrackingEngine.apply_action_env
    does — scope built for the *source* state — which is what actually
    calls actuator.defer."""
    automaton = project_service.get_automaton(PROJECT, db.get_project_published_revision(PROJECT))
    context = FixedProjectContext(automaton=automaton, project_id=PROJECT)
    builder = EvaluationScopeBuilder(
        PersistedEnv(db, context), MetricService(db, context), SessionFacts(db, context),
        UserFacts(db), db, None, factory.live(project_id=PROJECT),
    )
    scope = builder.build(automaton, "a", signal_values)
    return automaton.render_on_enter(automaton.states["a"].actions[0], scope)


def test_a_deferred_lambda_is_hibernated_as_source_plus_frozen_scope_under_user_and_project(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service)

    rendered = _defer_through_on_enter(file_db, factory, project_service, {"distress": 70})

    assert rendered is None  # defer itself tunnels nothing to the client
    (row,) = file_db.list_tasks()
    assert row["type"] == DeferredActuatorTask.TYPE
    assert row["status"] == "pending"
    assert row["username"] == USERNAME
    assert row["project_id"] == PROJECT
    assert row["run_at"].year == 2030
    assert row["ui_label"] == "Reminders · A → go: actuator.notify(user.name, 'high' if signal.distress > 50 else 'low')"
    assert "2030-01-01" in row["ui_description"]
    payload = row["payload"]
    assert payload["expression"] == "actuator.notify(user.name, 'high' if signal.distress > 50 else 'low')"
    assert payload["project_id"] == PROJECT
    assert payload["project_revision"] == file_db.get_project_published_revision(PROJECT)
    assert payload["state_key"] == "a"
    assert payload["action_name"] == "go"
    assert payload["snapshot"]["signal"] == {"distress": 70}
    assert payload["snapshot"]["user"]["name"] == "Ada"
    assert payload["snapshot"]["env"] == {}
    assert "session" not in payload["snapshot"]


def test_after_a_restart_the_lambda_runs_against_an_equivalent_environment(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service)
    _defer_through_on_enter(file_db, factory, project_service, {"distress": 70})
    (row,) = file_db.list_tasks()

    # Meanwhile, the world moves on: the user is renamed, the row comes
    # due, and the process that accepted the call is gone (never started
    # claiming, in fact).
    User.update(name="Grace").where(User.id == USERNAME).execute()
    TaskRow.update(run_at=datetime.utcnow() - timedelta(seconds=1)).where(TaskRow.key == row["key"]).execute()
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    assert websocket.sent == [{
        "type": "notification",
        # user.name is the frozen "Ada", not today's "Grace" — exactly what
        # the in-memory closure (a dict snapshot) would have reported;
        # signal.distress likewise comes from the snapshot.
        "on-enter": 'notify("Ada", "high")',
    }]


def test_the_lambda_runs_against_the_revision_it_was_deferred_from(file_db):
    """A republish between defer and run never reinterprets the call:
    the payload pins the revision and Archive keeps it."""
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service)
    _defer_through_on_enter(file_db, factory, project_service, {"distress": 10})
    (row,) = file_db.list_tasks()
    # Republish with the on-enter (and the state label) changed.
    _publish(file_db, project_service, INDEX_YML.replace("'high' if signal.distress > 50 else 'low'", "'changed'").replace("ui-label: A", "ui-label: A2"))
    assert file_db.get_project_published_revision(PROJECT) != row["payload"]["project_revision"]
    TaskRow.update(run_at=datetime.utcnow() - timedelta(seconds=1)).where(TaskRow.key == row["key"]).execute()
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    assert websocket.sent == [{"type": "notification", "on-enter": 'notify("Ada", "low")'}]


def test_a_deferred_call_can_defer_again_and_the_chain_is_hibernated_too(file_db):
    chain_yml = INDEX_YML.replace(
        "actuator.defer(lambda: actuator.notify(user.name, 'high' if signal.distress > 50 else 'low'), datetime.datetime(2030, 1, 1))",
        "actuator.defer(lambda: actuator.defer(lambda: actuator.celebrate(), datetime.datetime(2031, 1, 1)), datetime.datetime(2030, 1, 1))",
    )
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, chain_yml)
    _defer_through_on_enter(file_db, factory, project_service, {})
    (outer,) = file_db.list_tasks()
    TaskRow.update(run_at=datetime.utcnow() - timedelta(seconds=1)).where(TaskRow.key == outer["key"]).execute()

    _process(file_db, start=True)

    assert _wait_until(lambda: file_db.get_task(outer["key"])["status"] == "done"), file_db.get_task(outer["key"])
    inner = [row for row in file_db.list_tasks() if row["key"] != outer["key"]]
    assert len(inner) == 1
    assert inner[0]["status"] == "pending"
    assert inner[0]["run_at"].year == 2031
    assert inner[0]["payload"]["expression"] == "actuator.celebrate()"
    assert inner[0]["username"] == USERNAME and inner[0]["project_id"] == PROJECT


def test_deleting_the_project_takes_its_pending_deferred_calls_with_it(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service)
    _defer_through_on_enter(file_db, factory, project_service, {"distress": 70})
    assert file_db.list_tasks()

    asyncio.run(project_service.delete_project(PROJECT, lambda *_: asyncio.sleep(0)))

    assert file_db.list_tasks() == []


def test_a_deferred_call_never_sees_a_session(file_db):
    """Belt and braces on top of the build-time check: even a payload
    hand-written to reference session.* fails at run time with an
    unknown name, never with a stale session's data."""
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service)
    _defer_through_on_enter(file_db, factory, project_service, {"distress": 70})
    (row,) = file_db.list_tasks()
    import json
    payload = {**row["payload"], "expression": "actuator.notify(user.name, session.number_of_user_sessions())"}
    TaskRow.update(payload=json.dumps(payload), run_at=datetime.utcnow() - timedelta(seconds=1)).where(TaskRow.key == row["key"]).execute()

    _process(file_db, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "failed"), file_db.get_task(row["key"])
    assert "session" in file_db.get_task(row["key"])["error"]
