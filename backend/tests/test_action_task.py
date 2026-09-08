"""An action's task runs as an ActionTask, now or deferred — never
inline in the request that fired it — and what it produces reaches the
browser over the websocket. The task is hibernated as script + frozen
scope under (user, project, revision), so a brand-new JobService/factory
over the same database runs it against an equivalent environment: the
frozen part (user/signal/env) exactly as the in-turn evaluation would
have seen it, the live part (task.*, metric.*, ...) rebuilt for that
user and that project revision — with no session in scope.

task.* itself only carries send_mail/whatsapp/defer/prompt now —
celebrate/notify/show and switch_to_human/switch_to_ai moved to
chat.*, reachable only from an on-exit script, evaluated synchronously
by TrackingEngine.apply_action_env — never hibernated as an ActionTask
at all (see test_on_exit_chat_switch.py for that path end to end).
These tests therefore run every script in "fake" mode: `task.send_mail`/
`task.whatsapp` are the only namespace members that still wrap their
own argument into an observable JsSnippet report when real side
effects are suppressed (see FakeTaskNamespace) — a live task call has
no browser-visible output of its own any more.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta

import pytest

from automaton.automaton_builder import AutomatonBuilder
from chat.ws_notifications import WsNotifications
from conftest import FakeAiService, make_test_namespace_factory, make_test_job_service
from db import Db
from db.models import Task as TaskRow, User
from job import JobService
from metrics.metric_service import MetricService
from chat.sessions.session_manager import ChatSessionManager
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from tracking.actuators.action_task import TASK_NAMESPACE_LIVE, ActionTask, ScopeHydrator
from tracking.env import Env, PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
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
        task: |
          {task}
  b:
    ui-label: B
    contextual-prompt: there
"""

# task.send_mail's own fake-mode report embeds `to` verbatim (see
# FakeTaskNamespace._notify) — used throughout as the observable stand-in
# for the old actuator.notify(user.name, ...) pattern, now that
# celebrate/notify moved to chat (see this file's own module docstring).
#
# task.defer's own fake variant is a genuine no-op (never persists the
# inner task at all — always was, even before this rename, see
# FakeTaskNamespace.defer) — the defer-mechanics tests below must run
# live for defer() itself to actually schedule anything. Live mode's
# own task.whatsapp is the one task-only call safe to run for real
# there: `whatsapp-service` is never configured in these tests, so it
# short-circuits to `False` with no real network attempt, unlike
# send_mail (always a real notification_service.enqueue_mail attempt,
# against the dummy SMTP config — see conftest.make_test_namespace_factory's
# own docstring on why that must never actually run). Its own return
# carries no observable content though, so these tests prove the
# frozen-scope restore via the hibernated payload's own snapshot data
# directly, not via rendered wire text.
DEFER_LINE = "task.defer(lambda: task.whatsapp('34600000001', 'high' if signal.distress > 50 else 'low'), datetime.datetime(2030, 1, 1))"


def _yml(task: str) -> str:
    return INDEX_YML.replace("{task}", task)


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

    def send(self, payload: dict):
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
    instance = Db(f"sqlite:///{tmp_path / 'task.db'}")
    instance.get_or_create_user("test", "sub-user", "user", "Ada", None)
    return instance


def _publish(db: Db, project_service: ProjectService, index_yml: str) -> None:
    db.ensure_project(PROJECT)
    db.save_project_files(PROJECT, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT)
    db.set_active_project_id(PROJECT, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_project_id, _automaton):
        pass

    asyncio.run(project_service._manager.finalize_update(PROJECT, automaton, commit))


def _process(db: Db, websocket: _FakeWebSocket | None = None, *, start: bool = False, ai_service=None):
    """One "process": a JobService, a ProjectService and a namespace
    factory over `db`, wired the way main.py does — started only when
    asked, since a not-yet-started service is exactly what a process
    still wiring itself up looks like."""
    job_service = make_test_job_service(db)
    _live_services.append(job_service)
    project_service = ProjectService(db, AutomatonLoader(db), ChatSessionManager(db))
    factory = make_test_namespace_factory(db, job_service, project_service, ai_service)
    if websocket is not None:
        ws_notifications = WsNotifications(auth_service=None)
        ws_notifications._connections[USERNAME] = [websocket]
        factory.set_ws_notifications(ws_notifications)
    if start:
        job_service.start()
    return job_service, project_service, factory


def _fire_go(
    db: Db, factory, project_service: ProjectService, signal_values: dict, *, session_id: int | None = None,
    ai_service=None, fake: bool = True,
) -> None:
    """Applies state a's `go` action the way TrackingEngine.apply_transition
    does — env: synchronously, task as a task. Fake mode by default —
    see this file's own module docstring for why that's what's actually
    observable here now."""
    automaton = project_service.get_automaton(PROJECT, db.get_project_published_revision(PROJECT))
    context = FixedProjectContext(automaton=automaton, project_id=PROJECT)
    # Same branch production code takes (see PersistedEnv's own docstring
    # and tracking.actuators.action_task.ScopeHydrator): no real session
    # means a plain, in-memory Env(), never PersistedEnv(None).
    env = PersistedEnv(db, context, session_id=session_id) if session_id is not None else Env()
    task_namespace = factory.fake(project_id=PROJECT) if fake else factory.live(project_id=PROJECT)
    builder = EvaluationScopeBuilder(
        env, MetricService(db, context), SessionFacts(db, context),
        UserFacts(db), db, None, task_namespace, ai_service=ai_service,
    )
    engine = TrackingEngine(DbTrackingSink(db), env, builder)
    engine.apply_action_env(automaton, automaton.states["a"].actions[0], signal_values, "a", session_id=session_id)


def _due_now(key: str) -> None:
    TaskRow.update(run_at=datetime.utcnow() - timedelta(seconds=1)).where(TaskRow.key == key).execute()


# --- PersistedEnv requires a real session_id --------------------------------

def test_persisted_env_cannot_be_constructed_without_a_session_id(db):
    with pytest.raises(TypeError):
        PersistedEnv(db, FixedProjectContext(project_id=PROJECT))  # session_id omitted


def test_build_scope_with_no_session_never_constructs_a_persisted_env(file_db):
    """reset_test_sessions' own project-wide reset schedules an ActionTask
    with session_id=None (see ChatService._schedule_task) — build_scope
    must fall back to a plain, ephemeral Env() for that, never PersistedEnv
    (which now requires a real session_id — see its own constructor): this
    used to fall through to PersistedEnv(db, context) with none at all,
    which would have crashed on its first write (Tracking.session is a
    real FK) — now it fails fast, right here, if it regresses."""
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    hydrator = ScopeHydrator(file_db, project_service, factory, None)
    payload = {
        "project_id": PROJECT,
        "project_revision": file_db.get_project_published_revision(PROJECT),
        "state_key": "a",
        "snapshot": {},
        "namespace_kind": TASK_NAMESPACE_LIVE,
    }

    hydrator.build_scope(USERNAME, payload)  # must not raise


# --- immediate task ------------------------------------------------------

def test_a_task_is_hibernated_as_a_task_due_now_not_run_inline(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))

    _fire_go(file_db, factory, project_service, {"distress": 10})

    (row,) = file_db.list_tasks()
    assert row["type"] == ActionTask.TYPE
    assert row["status"] == "pending"  # the service is not started: nothing ran inline
    assert row["username"] == USERNAME and row["project_id"] == PROJECT
    assert row["run_at"] <= datetime.now(row["run_at"].tzinfo)
    assert row["ui_label"] == "Reminders · A → go: task.send_mail(user.name, 'welcome')"
    assert row["payload"]["script"].strip() == "task.send_mail(user.name, 'welcome')"
    assert row["payload"]["namespace_kind"] == "fake"
    assert row["payload"]["state_key"] == "a" and row["payload"]["action_name"] == "go"
    assert row["payload"]["snapshot"]["user"]["name"] == "Ada"
    assert "session" not in row["payload"]["snapshot"]


def test_a_task_reports_a_suppressed_send_mail_over_the_websocket_in_fake_mode(file_db):
    websocket = _FakeWebSocket()
    _, project_service, factory = _process(file_db, websocket, start=True)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))

    _fire_go(file_db, factory, project_service, {"distress": 10})

    assert _wait_until(lambda: websocket.sent), file_db.list_tasks()
    (frame,) = websocket.sent
    assert frame["type"] == "notification"
    assert "send_mail(to='Ada')" in frame["task"]
    assert "Run actuators is off" in frame["task"]
    assert _wait_until(lambda: file_db.list_tasks()[0]["status"] == "done")


def test_task_prompt_runs_inside_the_task_with_the_firing_sessions_history(file_db):
    """The model call happens on a worker, never in the request; its
    conversation history is the firing session's, still there. Its own
    reply text is only observable here via the fake-mode wrapper's own
    `to` argument (see this file's module docstring) — task.prompt's
    result is passed as `to` deliberately, just to make it visible."""
    websocket = _FakeWebSocket()
    ai_service = FakeAiService()
    _, project_service, factory = _process(file_db, websocket, start=True, ai_service=ai_service)
    _publish(file_db, project_service, _yml("task.send_mail(task.prompt('Recap the last exchange.'), 'note')"))
    file_db.create_chat_session(username=USERNAME, project_id=PROJECT, revision=file_db.get_project_published_revision(PROJECT))
    session_id = file_db.get_latest_chat_session(USERNAME, PROJECT)["id"]

    _fire_go(file_db, factory, project_service, {"distress": 10}, session_id=session_id, ai_service=ai_service)

    (row,) = file_db.list_tasks()
    assert row["payload"]["session_id"] == session_id
    assert _wait_until(lambda: websocket.sent), file_db.list_tasks()
    (frame,) = websocket.sent
    assert "send_mail(to='Fake AI reply.')" in frame["task"]
    assert file_db.get_messages(session_id) == []  # read-only, as before


def test_a_fake_task_namespaces_task_still_runs_as_a_task_and_reports(file_db):
    """Test session with "Run actuators" off: send_mail/whatsapp are
    both suppressed and reported through the same task path."""
    websocket = _FakeWebSocket()
    _, project_service, factory = _process(file_db, websocket, start=True)
    _publish(file_db, project_service, _yml("task.send_mail(user.email, 'hi')\n          task.whatsapp('34600000001', 'hi')"))
    automaton = project_service.get_automaton(PROJECT, file_db.get_project_published_revision(PROJECT))
    context = FixedProjectContext(automaton=automaton, project_id=PROJECT)
    env = PersistedEnv(file_db, context, session_id=0)
    builder = EvaluationScopeBuilder(
        env, MetricService(file_db, context), SessionFacts(file_db, context), UserFacts(file_db), file_db, None,
        factory.fake(project_id=PROJECT),
    )
    TrackingEngine(DbTrackingSink(file_db), env, builder).apply_action_env(
        automaton, automaton.states["a"].actions[0], {}, "a",
    )

    (row,) = file_db.list_tasks()
    assert row["payload"]["namespace_kind"] == "fake"
    assert _wait_until(lambda: websocket.sent), file_db.list_tasks()
    (frame,) = websocket.sent
    assert "Run actuators is off" in frame["task"]
    assert frame["task"].endswith("no message was sent.\")")


def test_a_task_survives_a_restart_and_runs_against_an_equivalent_environment(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'high' if signal.distress > 50 else 'low')"))
    _fire_go(file_db, factory, project_service, {"distress": 70})
    (row,) = file_db.list_tasks()
    # The process that accepted it is gone (never started claiming);
    # meanwhile the user is renamed.
    User.update(name="Grace").where(User.id == USERNAME).execute()
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    # user.name is the frozen "Ada", never the live-renamed "Grace" —
    # exactly what the in-turn evaluation would have seen.
    (frame,) = websocket.sent
    assert "send_mail(to='Ada')" in frame["task"]


# --- deferred ----------------------------------------------------------------

def test_a_deferred_lambda_is_the_same_task_with_a_later_when_and_no_session(file_db):
    _, project_service, factory = _process(file_db, start=True)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    file_db.create_chat_session(username=USERNAME, project_id=PROJECT, revision=file_db.get_project_published_revision(PROJECT))
    session_id = file_db.get_latest_chat_session(USERNAME, PROJECT)["id"]

    _fire_go(file_db, factory, project_service, {"distress": 70}, session_id=session_id, fake=False)

    # The outer task runs now and, running, hibernates the inner one.
    assert _wait_until(lambda: len(file_db.list_tasks()) == 2 and all(r["status"] in ("done", "pending") for r in file_db.list_tasks()))
    outer = next(r for r in file_db.list_tasks() if r["payload"]["session_id"] == session_id)
    inner = next(r for r in file_db.list_tasks() if r["key"] != outer["key"])
    assert outer["status"] == "done"
    assert inner["status"] == "pending"
    assert inner["run_at"].year == 2030
    assert inner["payload"]["script"] == "task.whatsapp('34600000001', 'high' if signal.distress > 50 else 'low')"
    assert inner["payload"]["session_id"] is None
    assert inner["payload"]["snapshot"]["signal"] == {"distress": 70}
    assert inner["payload"]["snapshot"]["user"]["name"] == "Ada"
    assert inner["ui_label"] == "Reminders · A → go: task.whatsapp('34600000001', 'high' if signal.distress > 50 else 'low')"
    assert "Deferred by" in inner["ui_description"]


def test_a_deferred_call_runs_after_a_restart_against_the_frozen_scope(file_db):
    _, project_service, factory = _process(file_db, start=True)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    _fire_go(file_db, factory, project_service, {"distress": 70}, fake=False)
    assert _wait_until(lambda: len(file_db.list_tasks()) == 2)
    inner = next(r for r in file_db.list_tasks() if r["status"] == "pending")
    # The frozen scope this inner task restores from is what actually
    # matters (proven directly here) — the live-mode call it runs
    # (task.whatsapp, safely no-op with no whatsapp-service configured)
    # has no wire-observable content of its own any more, unlike the
    # old actuator.notify(...) pattern (see this file's own module
    # docstring on why).
    assert inner["payload"]["snapshot"]["signal"] == {"distress": 70}
    assert inner["payload"]["snapshot"]["user"]["name"] == "Ada"
    User.update(name="Grace").where(User.id == USERNAME).execute()
    _due_now(inner["key"])
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    assert _wait_until(lambda: file_db.get_task(inner["key"])["status"] == "done"), file_db.get_task(inner["key"])


def test_a_deferred_lambda_sees_names_assigned_earlier_in_the_same_script(file_db):
    script = (
        "greeting = 'hello ' + user.name\n"
        "          task.defer(lambda: task.whatsapp('34600000001', greeting), datetime.datetime(2030, 1, 1))"
    )
    _, project_service, factory = _process(file_db, start=True)
    _publish(file_db, project_service, _yml(script))
    _fire_go(file_db, factory, project_service, {}, fake=False)
    assert _wait_until(lambda: len(file_db.list_tasks()) == 2)
    inner = next(r for r in file_db.list_tasks() if r["status"] == "pending")
    assert inner["payload"]["snapshot"]["extra"] == {"greeting": "hello Ada"}
    _due_now(inner["key"])
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    assert _wait_until(lambda: file_db.get_task(inner["key"])["status"] == "done"), file_db.get_task(inner["key"])


def test_the_task_runs_against_the_revision_it_was_written_for(file_db):
    """A republish between scheduling and running never reinterprets the
    script: the payload pins the revision and Archive keeps it."""
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'old')"))
    _fire_go(file_db, factory, project_service, {})
    (row,) = file_db.list_tasks()
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'new')").replace("ui-label: A", "ui-label: A2"))
    assert file_db.get_project_published_revision(PROJECT) != row["payload"]["project_revision"]
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    (frame,) = websocket.sent
    assert "send_mail(to='Ada')" in frame["task"]


def test_deleting_the_project_takes_its_pending_tasks_with_it(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    _fire_go(file_db, factory, project_service, {"distress": 70}, fake=False)
    assert file_db.list_tasks()

    asyncio.run(project_service.delete_project(PROJECT, lambda *_: asyncio.sleep(0)))

    assert file_db.list_tasks() == []


def test_a_task_never_sees_a_session(file_db):
    """Belt and braces on top of the build-time check: even a payload
    hand-written to reference session.* fails at run time with an
    unknown name, never with a stale session's data."""
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'x')"))
    _fire_go(file_db, factory, project_service, {})
    (row,) = file_db.list_tasks()
    payload = {**row["payload"], "script": "task.send_mail(user.name, session.number_of_user_sessions())"}
    TaskRow.update(payload=json.dumps(payload)).where(TaskRow.key == row["key"]).execute()
    websocket = _FakeWebSocket()

    _process(file_db, websocket, start=True)

    # render_task_script logs and skips a failing statement, same as
    # the in-turn evaluation always did: the task settles done with nothing to push.
    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    assert websocket.sent == []
