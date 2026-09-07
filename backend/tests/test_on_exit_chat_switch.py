"""chat.switch_to_human(user_id)/chat.switch_to_ai() and the rest of
chat.* now run exclusively from an action's own on-exit script,
synchronously inside TrackingEngine.apply_action_env — never as a
background ActionTask (that's task.*'s own job, see
tracking/actuators/action_task.py). This is the on-exit/chat
equivalent of what test_action_task.py exercises for task.*, end to
end against a real TaskNamespaceFactory/WsNotifications pair."""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from chat.ws_notifications import WsNotifications
from conftest import make_test_namespace_factory, make_test_job_service
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT = "on_exit_chat"

INDEX_YML = """
project:
  id: on_exit_chat
  ui-label: On exit chat
init-action:
  target: a
env:
  counter:
    value: 0
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-exit: |
          {on_exit}
  b:
    ui-label: B
    contextual-prompt: there
"""


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, payload: dict):
        self.sent.append(payload)


def _publish(db, project_service: ProjectService, on_exit: str) -> None:
    index_yml = INDEX_YML.replace("{on_exit}", on_exit.replace("\n", "\n          "))
    db.ensure_project(PROJECT)
    db.save_project_files(PROJECT, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT)
    db.set_active_project_id(PROJECT, USERNAME)
    import asyncio
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_project_id, _automaton):
        pass

    asyncio.run(project_service._manager.finalize_update(PROJECT, automaton, commit))


def _fire_go(db, factory, project_service: ProjectService, session_id: int, *, fake: bool = False) -> None:
    automaton = project_service.get_automaton(PROJECT, db.get_project_published_revision(PROJECT))
    context = FixedProjectContext(automaton=automaton, project_id=PROJECT)
    env = PersistedEnv(db, context, session_id=session_id)
    task_namespace = factory.fake(project_id=PROJECT) if fake else factory.live(project_id=PROJECT)
    chat_namespace = factory.chat_fake(project_id=PROJECT) if fake else factory.chat_live(project_id=PROJECT)
    task_namespace = task_namespace.with_session(session_id)
    chat_namespace = chat_namespace.with_session(session_id)
    builder = EvaluationScopeBuilder(
        env, MetricService(db, context), SessionFacts(db, context), UserFacts(db), db, None,
        task_namespace, chat_namespace,
    )
    engine = TrackingEngine(DbTrackingSink(db), env, builder)
    engine.apply_action_env(automaton, automaton.states["a"].actions[0], {}, "a", session_id=session_id)


@pytest.fixture
def wired(db):
    job_service = make_test_job_service(db)
    project_service = ProjectService(db)
    factory = make_test_namespace_factory(db, job_service, project_service)
    ws_notifications = WsNotifications(auth_service=None)
    factory.set_ws_notifications(ws_notifications)
    return db, project_service, factory, ws_notifications


def _session(db, project_service: ProjectService) -> int:
    db.create_chat_session(username=USERNAME, project_id=PROJECT, revision=db.get_project_published_revision(PROJECT))
    return db.get_latest_chat_session(USERNAME, PROJECT)["id"]


def test_switch_to_human_from_on_exit_records_the_operator_and_pages_them(wired):
    db, project_service, factory, ws_notifications = wired
    admin_socket = _FakeWebSocket()
    ws_notifications._connections["admin"] = [admin_socket]
    _publish(db, project_service, "chat.switch_to_human('admin')")
    session_id = _session(db, project_service)

    _fire_go(db, factory, project_service, session_id)

    assert factory.get_human_operator(session_id) == "admin"
    assert admin_socket.sent == [{"type": "human_takeover", "session_id": session_id, "project_id": PROJECT}]


def test_switch_to_ai_from_on_exit_clears_a_previously_set_operator(wired):
    db, project_service, factory, _ws_notifications = wired
    _publish(db, project_service, "chat.switch_to_ai()")
    session_id = _session(db, project_service)
    factory.set_human_operator(session_id, "admin")

    _fire_go(db, factory, project_service, session_id)

    assert factory.get_human_operator(session_id) is None


def test_a_fake_chat_namespace_suppresses_switch_to_human_and_reports_it(wired):
    """Test session with "Run actuators" off: nobody is actually paged
    — same suppress-and-report shape task.* gets for send_mail/whatsapp/defer."""
    db, project_service, factory, ws_notifications = wired
    user_socket = _FakeWebSocket()
    ws_notifications._connections[USERNAME] = [user_socket]
    _publish(db, project_service, "chat.switch_to_human('admin')")
    session_id = _session(db, project_service)

    _fire_go(db, factory, project_service, session_id, fake=True)

    assert factory.get_human_operator(session_id) is None
    (frame,) = user_socket.sent
    assert frame["type"] == "notification"
    assert "Run actuators is off" in frame["task"]
    assert "no one was paged" in frame["task"]


def test_a_mixed_on_exit_script_writes_env_and_pushes_a_chat_notification_synchronously(wired):
    """No background ActionTask involved at all — the push happens
    inline, in the same call that applies the env write."""
    db, project_service, factory, ws_notifications = wired
    user_socket = _FakeWebSocket()
    ws_notifications._connections[USERNAME] = [user_socket]
    _publish(db, project_service, "env.counter = env.counter + 1\nchat.celebrate()\nchat.notify('Nice!', 'Done.')")
    session_id = _session(db, project_service)
    db.set_action_env(session_id, {"counter": 0})

    _fire_go(db, factory, project_service, session_id)

    assert db.get_action_env(PROJECT, USERNAME).get("counter") == 1
    assert user_socket.sent == [{"type": "notification", "task": 'celebrate()\nnotify("Nice!", "Done.")'}]
    # Never hibernated as a Task: on-exit's own chat.* never goes through ActionTask.
    assert db.list_tasks() == []
