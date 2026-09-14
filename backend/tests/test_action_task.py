"""An action's task runs as an ActionTask, now or deferred — never
inline in the request that fired it — and what it produces reaches the
browser over the websocket. The task is hibernated as script + frozen
scope under (user, project, revision), so a brand-new SchedulerService/factory
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
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from automaton.automaton_builder import AutomatonBuilder
from system.bus import TASK_ENDED, TASK_STARTED, UI_NOTIFICATION
from conftest import RecordedMessages, FakeAiService, make_test_namespace_factory, make_test_scheduler_service
from db import Db
from db.models import Task as TaskRow, User
from metrics.metric_service import MetricService
from turn.sessions.session_manager import SessionManager
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from scheduler import SchedulerService
from tracking.actuators.action_task import (
    TASK_NAMESPACE_FAKE, TASK_NAMESPACE_LIVE, ActionTask, AnnouncedActionTask, ScopeHydrator,
)
from tracking.env import Env, PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from system.web_session import WebSession
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




_live_services: list[SchedulerService] = []


@pytest.fixture(autouse=True)
def _stop_services():
    yield
    while _live_services:
        _live_services.pop().stop()


@pytest.fixture
def file_db(tmp_path) -> Db:
    instance = Db(f"sqlite:///{tmp_path / 'task.db'}")
    instance.get_or_create_user("test", "sub-user", "user", "Ada", None)
    return instance


def _publish(db: Db, project_service: ProjectService, index_yml: str) -> None:
    db.ensure_project(PROJECT)
    db.save_project_files(PROJECT, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT)
    db.set_active_project_id(PROJECT, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})


    asyncio.run(project_service.manager.finalize_update(PROJECT, automaton))


def _process(db: Db, *, start: bool = False, ai_service=None):
    """One "process": a SchedulerService, a ProjectService and a namespace
    factory over `db`, wired the way main.py does — started only when
    asked, since a not-yet-started service is exactly what a process
    still wiring itself up looks like."""
    scheduler_service = make_test_scheduler_service(db)
    _live_services.append(scheduler_service)
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    factory = make_test_namespace_factory(db, scheduler_service, project_service, ai_service)
    if start:
        scheduler_service.start()
    return scheduler_service, project_service, factory


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


def test_persisted_env_cannot_be_constructed_without_a_session_id(db):
    with pytest.raises(TypeError):
        PersistedEnv(db, FixedProjectContext(project_id=PROJECT))


def test_build_scope_with_no_session_never_constructs_a_persisted_env(file_db):
    """reset_test_sessions' own project-wide reset schedules an ActionTask
    with session_id=None (see TurnService._schedule_task) — build_scope
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

    hydrator.build_scope(USERNAME, payload)


def test_a_task_is_hibernated_as_a_task_due_now_not_run_inline(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))

    _fire_go(file_db, factory, project_service, {"distress": 10})

    (row,) = file_db.list_tasks()
    assert row["type"] == ActionTask.TYPE
    assert row["status"] == "pending"
    assert row["username"] == USERNAME and row["project_id"] == PROJECT
    assert row["run_at"] <= datetime.now(row["run_at"].tzinfo)
    assert row["ui_label"] == "Reminders · A → go: task.send_mail(user.name, 'welcome')"
    assert row["payload"]["script"].strip() == "task.send_mail(user.name, 'welcome')"
    assert row["payload"]["namespace_kind"] == "fake"
    assert row["payload"]["state_key"] == "a" and row["payload"]["action_name"] == "go"
    assert row["payload"]["snapshot"]["user"]["name"] == "Ada"
    assert "session" not in row["payload"]["snapshot"]


def test_a_task_reports_a_suppressed_send_mail_in_fake_mode(file_db):
    notified = RecordedMessages(UI_NOTIFICATION)
    _, project_service, factory = _process(file_db, start=True)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))

    _fire_go(file_db, factory, project_service, {"distress": 10})

    assert _wait_until(lambda: notified.for_user(USERNAME)), file_db.list_tasks()
    (message,) = notified.for_user(USERNAME)
    assert "send_mail(to='Ada')" in message.body["task"]
    assert "Run actuators is off" in message.body["task"]
    assert _wait_until(lambda: file_db.list_tasks()[0]["status"] == "done")


def test_task_prompt_runs_inside_the_task_with_the_firing_sessions_history(file_db):
    """The model call happens on a worker, never in the request; its
    conversation history is the firing session's, still there. Its own
    reply text is only observable here via the fake-mode wrapper's own
    `to` argument (see this file's module docstring) — task.prompt's
    result is passed as `to` deliberately, just to make it visible."""
    notified = RecordedMessages(UI_NOTIFICATION)
    ai_service = FakeAiService()
    _, project_service, factory = _process(file_db, start=True, ai_service=ai_service)
    _publish(file_db, project_service, _yml("task.send_mail(task.prompt('Recap the last exchange.'), 'note')"))
    file_db.create_chat_session(username=USERNAME, project_id=PROJECT, revision=file_db.get_project_published_revision(PROJECT))
    session_id = file_db.get_latest_chat_session(USERNAME, PROJECT)["id"]

    _fire_go(file_db, factory, project_service, {"distress": 10}, session_id=session_id, ai_service=ai_service)

    (row,) = file_db.list_tasks()
    assert row["payload"]["session_id"] == session_id
    assert _wait_until(lambda: notified.for_user(USERNAME)), file_db.list_tasks()
    (message,) = notified.for_user(USERNAME)
    assert "send_mail(to='Fake AI reply.')" in message.body["task"]
    assert file_db.get_messages(session_id) == []


def test_a_fake_task_namespaces_task_still_runs_as_a_task_and_reports(file_db):
    """Test session with "Run actuators" off: send_mail/whatsapp are
    both suppressed and reported through the same task path."""
    notified = RecordedMessages(UI_NOTIFICATION)
    _, project_service, factory = _process(file_db, start=True)
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
    assert _wait_until(lambda: notified.for_user(USERNAME)), file_db.list_tasks()
    (message,) = notified.for_user(USERNAME)
    assert "Run actuators is off" in message.body["task"]
    assert message.body["task"].endswith("no message was sent.\")")


def test_a_task_survives_a_restart_and_runs_against_an_equivalent_environment(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'high' if signal.distress > 50 else 'low')"))
    _fire_go(file_db, factory, project_service, {"distress": 70})
    (row,) = file_db.list_tasks()
    User.update(name="Grace").where(User.id == USERNAME).execute()
    notified = RecordedMessages(UI_NOTIFICATION)

    _process(file_db, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    (message,) = notified.for_user(USERNAME)
    assert "send_mail(to='Ada')" in message.body["task"]


def test_a_deferred_lambda_is_the_same_task_with_a_later_when_and_no_session(file_db):
    _, project_service, factory = _process(file_db, start=True)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    file_db.create_chat_session(username=USERNAME, project_id=PROJECT, revision=file_db.get_project_published_revision(PROJECT))
    session_id = file_db.get_latest_chat_session(USERNAME, PROJECT)["id"]

    _fire_go(file_db, factory, project_service, {"distress": 70}, session_id=session_id, fake=False)

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
    assert inner["payload"]["snapshot"]["signal"] == {"distress": 70}
    assert inner["payload"]["snapshot"]["user"]["name"] == "Ada"
    User.update(name="Grace").where(User.id == USERNAME).execute()
    _due_now(inner["key"])

    _process(file_db, start=True)

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

    _process(file_db, start=True)

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
    notified = RecordedMessages(UI_NOTIFICATION)

    _process(file_db, start=True)

    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "done"), file_db.get_task(row["key"])
    (message,) = notified.for_user(USERNAME)
    assert "send_mail(to='Ada')" in message.body["task"]


def test_deleting_the_project_takes_its_pending_tasks_with_it(file_db):
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    _fire_go(file_db, factory, project_service, {"distress": 70}, fake=False)
    assert file_db.list_tasks()

    asyncio.run(project_service.delete_project(PROJECT))

    assert file_db.list_tasks() == []


def test_a_task_never_sees_a_session(file_db):
    """Belt and braces on top of the build-time check: even a payload
    hand-written to reference session.* fails at run time with an
    unknown name, never with a stale session's data — and the row says
    so, naming the statement that raised."""
    _, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'x')"))
    _fire_go(file_db, factory, project_service, {})
    (row,) = file_db.list_tasks()
    payload = {**row["payload"], "script": "task.send_mail(user.name, session.number_of_user_sessions())"}
    TaskRow.update(payload=json.dumps(payload)).where(TaskRow.key == row["key"]).execute()
    notified = RecordedMessages(UI_NOTIFICATION)

    _process(file_db, start=True)
    assert _wait_until(lambda: file_db.get_task(row["key"])["status"] == "failed"), file_db.get_task(row["key"])
    assert "session" in file_db.get_task(row["key"])["error"]
    assert notified.messages == []


def _hibernated(
    db: Db, project_service: ProjectService, factory, *,
    session_id: int | None = None, ai_service=None, fake: bool = True, running_script: str | None = None,
    plain: bool = False,
):
    _fire_go(db, factory, project_service, {"distress": 10}, session_id=session_id, ai_service=ai_service, fake=fake)
    (row,) = db.list_tasks()
    payload = {**row["payload"], **({"script": running_script} if running_script else {})}
    return _rehydrated(
        db, project_service, factory, row["key"], payload, ai_service=ai_service, plain=plain,
    )


def _rehydrated(
    db: Db, project_service: ProjectService, factory, key: str, payload: dict, *,
    ai_service=None, plain: bool = False,
):
    hydrator = ScopeHydrator(db, project_service, factory, ai_service)
    task = ActionTask(key, USERNAME, payload, hydrator) if plain else hydrator.hydrate(key, USERNAME, payload)
    task.prepare()
    return task


def _session_of(db: Db) -> int:
    db.create_chat_session(username=USERNAME, project_id=PROJECT, revision=db.get_project_published_revision(PROJECT))
    return db.get_latest_chat_session(USERNAME, PROJECT)["id"]


def _drive(task) -> None:
    asyncio.run(task.run_next_step())


def test_a_task_announces_its_start_and_its_end_with_what_the_script_produced(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    recorded = RecordedMessages(TASK_STARTED, TASK_ENDED, UI_NOTIFICATION)
    task = _hibernated(file_db, project_service, factory)

    _drive(task)

    started, notified, ended = recorded.for_user(USERNAME)
    assert started.type == TASK_STARTED and started.body == {"key": task.key}
    assert notified.type == UI_NOTIFICATION
    assert ended.type == TASK_ENDED
    assert ended.body["key"] == task.key
    assert ended.body["result"] == notified.body["task"]
    assert ended.body["error"] is None


def test_an_action_task_on_its_own_pushes_its_snippets_and_says_nothing_else(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    recorded = RecordedMessages(TASK_STARTED, TASK_ENDED, UI_NOTIFICATION)
    task = _hibernated(file_db, project_service, factory, plain=True)

    _drive(task)

    assert type(task) is ActionTask
    assert [message.type for message in recorded.for_user(USERNAME)] == [UI_NOTIFICATION]


def test_a_hibernated_row_comes_back_as_the_task_that_announces_itself(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    _fire_go(file_db, factory, project_service, {"distress": 10})
    (row,) = file_db.list_tasks()

    hydrator = ScopeHydrator(file_db, project_service, factory, None)
    task = hydrator.hydrate(row["key"], USERNAME, row["payload"])

    assert isinstance(task, AnnouncedActionTask)
    assert task.TYPE == ActionTask.TYPE
    assert task.payload == row["payload"]


def test_a_failing_statement_is_announced_while_the_others_still_reach_the_browser(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    recorded = RecordedMessages(TASK_STARTED, TASK_ENDED, UI_NOTIFICATION)
    task = _hibernated(
        file_db, project_service, factory,
        running_script="task.send_mail(session.number_of_user_sessions(), 'x')\ntask.send_mail(user.name, 'welcome')",
    )

    with pytest.raises(Exception) as raised:
        _drive(task)

    (notified,) = recorded.of_type(UI_NOTIFICATION)
    assert "send_mail(to='Ada')" in notified.body["task"]
    (ended,) = recorded.of_type(TASK_ENDED)
    assert ended.body["result"] == notified.body["task"]
    assert "session.number_of_user_sessions()" in ended.body["error"]
    assert str(raised.value) == ended.body["error"]


def test_a_script_that_never_ran_is_announced_as_ended_with_no_result(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    recorded = RecordedMessages(TASK_STARTED, TASK_ENDED, UI_NOTIFICATION)
    broken = _hibernated(file_db, project_service, factory, running_script="task.send_mail('unclosed")

    with pytest.raises(SyntaxError):
        _drive(broken)

    assert recorded.of_type(UI_NOTIFICATION) == []
    (ended,) = recorded.of_type(TASK_ENDED)
    assert ended.body["result"] is None
    assert ended.body["error"]


def test_a_deferred_task_announces_itself_with_no_session_on_the_envelope(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    session_id = _session_of(file_db)
    outer = _hibernated(file_db, project_service, factory, session_id=session_id, fake=False)
    _drive(outer)
    inner = next(r for r in file_db.list_tasks() if r["payload"]["session_id"] is None)
    task = _rehydrated(file_db, project_service, factory, inner["key"], inner["payload"])
    recorded = RecordedMessages(TASK_STARTED, TASK_ENDED)

    _drive(task)

    for message in recorded.for_user(USERNAME):
        assert message.session_id is None
        assert message.project_id == PROJECT


def test_a_notification_says_which_conversation_its_script_is_about(file_db):
    scheduler, project_service, factory = _process(file_db)
    _publish(file_db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    session_id = _session_of(file_db)
    recorded = RecordedMessages(UI_NOTIFICATION)
    task = _hibernated(file_db, project_service, factory, session_id=session_id)

    _drive(task)

    (notified,) = recorded.of_type(UI_NOTIFICATION)
    assert notified.project_id == PROJECT
    assert notified.session_id == session_id


class _RecordingAiService(FakeAiService):

    def __init__(self) -> None:
        super().__init__()
        self.seen_as: list[str | None] = []

    async def prompt(self, prompt: str, channels=None):
        self.seen_as.append(WebSession().user)
        return await super().prompt(prompt, channels)


def test_a_task_prompt_runs_as_the_user_the_task_belongs_to(file_db):
    ai_service = _RecordingAiService()
    scheduler, project_service, factory = _process(file_db, ai_service=ai_service)
    _publish(file_db, project_service, _yml("task.send_mail(task.prompt('hi'), 'note')"))
    _fire_go(file_db, factory, project_service, {"distress": 10}, ai_service=ai_service)
    (row,) = file_db.list_tasks()
    task = _rehydrated(file_db, project_service, factory, row["key"], row["payload"], ai_service=ai_service)

    _drive(task)

    assert ai_service.seen_as == [USERNAME]


@contextmanager
def _task_scope(db: Db, project_service: ProjectService, factory):
    _publish(db, project_service, _yml("task.send_mail(user.name, 'welcome')"))
    payload = {
        "project_id": PROJECT,
        "project_revision": db.get_project_published_revision(PROJECT),
        "state_key": "a",
        "action_name": "go",
        "snapshot": {"user": {"name": "Ada"}},
        "namespace_kind": TASK_NAMESPACE_FAKE,
    }
    hydrator = ScopeHydrator(db, project_service, factory, None)
    with WebSession().impersonate(USERNAME):
        yield hydrator.build_scope(USERNAME, payload)


def test_a_script_whose_statements_all_succeed_reports_no_failure(file_db):
    scheduler, project_service, factory = _process(file_db)
    with _task_scope(file_db, project_service, factory) as scope:
        outcome = scope.automaton.render_task_script(
            "greeting = 'hi ' + user.name\ntask.send_mail(greeting, 'x')", scope,
        )

    assert outcome.failures == ()
    assert "send_mail(to='hi Ada')" in outcome.snippets


def test_a_statement_that_raises_is_collected_and_the_ones_after_it_still_run(file_db):
    scheduler, project_service, factory = _process(file_db)
    script = "task.send_mail(missing.who, 'x')\ntask.send_mail(user.name, 'welcome')"
    with _task_scope(file_db, project_service, factory) as scope:
        outcome = scope.automaton.render_task_script(script, scope)

    ((statement, exc),) = outcome.failures
    assert statement == "task.send_mail(missing.who, 'x')"
    assert isinstance(exc, Exception)
    assert "send_mail(to='Ada')" in outcome.snippets
    assert "missing" not in outcome.snippets


def test_a_script_that_does_not_parse_raises_rather_than_reporting_a_partial_run(file_db):
    scheduler, project_service, factory = _process(file_db)
    with _task_scope(file_db, project_service, factory) as scope:
        with pytest.raises(SyntaxError):
            scope.automaton.render_task_script("task.send_mail('unclosed", scope)


def test_a_pending_row_left_by_a_dead_process_announces_itself_when_it_finally_runs(file_db):
    scheduler, project_service, factory = _process(file_db, start=True)
    _publish(file_db, project_service, _yml(DEFER_LINE))
    _fire_go(file_db, factory, project_service, {"distress": 70}, fake=False)
    assert _wait_until(lambda: len(file_db.list_tasks()) == 2), file_db.list_tasks()
    inner = next(r for r in file_db.list_tasks() if r["status"] == "pending")
    scheduler.stop()
    _due_now(inner["key"])
    recorded = RecordedMessages(TASK_STARTED, TASK_ENDED)

    _process(file_db, start=True)

    assert _wait_until(lambda: file_db.get_task(inner["key"])["status"] == "done"), file_db.get_task(inner["key"])
    started, ended = recorded.for_user(USERNAME)
    assert started.type == TASK_STARTED and started.body == {"key": inner["key"]}
    assert ended.type == TASK_ENDED and ended.body["key"] == inner["key"]
    assert ended.body["error"] is None
    assert started.session_id is None and started.project_id == PROJECT
