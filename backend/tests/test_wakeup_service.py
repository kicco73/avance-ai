"""Cross-project wake-up, end to end: a self-loop action in one project
("watcher") references another ("observed") via automaton.*. A real
transition in "observed" publishes StateChanged, the reverse index
resolves it back to "watcher", and re-evaluating its triggers fires
the self-loop, recording a new transition.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from automaton.automaton_builder import AutomatonBuilder
from chat.ws_notifications import WsNotifications
from events import StateChanged, publish
from conftest import make_test_actuator_factory, make_test_job_service
from project.project_service import ProjectService
from tracking.wakeup_service import WakeupService

pytestmark = pytest.mark.contract

USERNAME = "user"

OBSERVED_YML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
  b:
    ui-label: B
    contextual-prompt: there
"""

WATCHER_YML = """
init-action:
  target: x
states:
  x:
    ui-label: X
    contextual-prompt: hi
    actions:
      - name: notice
        target: x
        trigger: "automaton.observed.state == 'b'"
"""


def _publish_project(db, project_service: ProjectService, project_name: str, index_yml: str) -> None:
    """A real save, through _finalize_project_update, so the reverse
    index is actually refreshed, same as a real save (put_project/
    put_project_file) does."""
    # Auto-declares `project: {id: <project_name>, family: test}` whenever
    # project_name is a valid identifier — both "observed" and "watcher"
    # share family "test" so automaton.observed references actually resolve.
    if project_name.isidentifier() and "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_name}\n  family: test\n{index_yml}"
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    # _finalize_project_update calls get_active_project_id(), which
    # raises when nothing is active yet — this bare helper has no
    # activation flow of its own to make that true.
    db.set_active_project_id(project_name, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_project_name, _automaton):
        pass

    asyncio.run(project_service._manager.finalize_update(project_name, automaton, commit))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


_actuator_factory = make_test_actuator_factory


class _FakeTrackingService:
    def __init__(self, disabled_session_ids):
        self._disabled = disabled_session_ids

    def is_auto_tracking_enabled(self, session_id):
        return session_id not in self._disabled


class _FakeWebSocket:
    """Just enough to stand in for a real connection in WsAdapter's
    username -> WebSocket _connections registry — push only calls
    send_json on it."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, payload: dict):
        self.sent.append(payload)


def _both_projects(db, project_service, *, observed_moved: bool = True) -> dict:
    """Publishes both projects, opens a session in each, and (by default)
    moves "observed" to state 'b' — the state the watcher's own self-loop
    trigger is actually watching for."""
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_id="watcher", revision=db.get_project_published_revision("watcher"))
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    if observed_moved:
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")
    return watcher_session


def _wake(db, project_service, **kwargs) -> None:
    service = WakeupService(db, project_service, make_test_job_service(db), _actuator_factory(db), **kwargs)
    asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))


def test_the_reverse_index_records_the_reference_and_is_cleared_once_it_is_removed(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)

    assert db.get_observers("observed") == ["watcher"]
    assert db.get_observed_projects("watcher") == ["observed"]

    _publish_project(db, project_service, "watcher", OBSERVED_YML)  # no longer references automaton.observed at all

    assert db.get_observers("observed") == []


def test_reevaluating_fires_the_self_loop_only_once_the_observed_state_actually_matches(db, project_service):
    quiet_session = _both_projects(db, project_service, observed_moved=False)
    before = len(db.get_signals(quiet_session["id"]))

    _wake(db, project_service)
    assert len(db.get_signals(quiet_session["id"])) == before

    observed_session = db.get_latest_chat_session(USERNAME, "observed")
    db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

    _wake(db, project_service)

    after = db.get_signals(quiet_session["id"])
    assert len(after) == before + 1
    assert after[-1]["old_state"] == "x"
    assert after[-1]["new_state"] == "x"  # self-loop — the state itself never changes
    assert after[-1]["origin"] == "system"


class TestWsAdapterPush:
    """A fired self-loop wake-up pushes a "notification" frame (state/
    project_name) to whichever connection is registered for
    `username`, never keyed on project_id (which only rides along
    inside the payload). The payload key is deliberately still
    "project_name", not "project_id": chatClient.js (frontend, off-limits)
    parses this exact WS message shape by that literal key name — see
    WakeupService._reevaluate_and_apply's own comment."""

    def _connected(self):
        ws_notifications = WsNotifications(auth_service=None)
        websocket = _FakeWebSocket()
        ws_notifications._connections[USERNAME] = [websocket]
        return ws_notifications, websocket

    def test_a_fired_self_loop_pushes_the_state_and_project_name_but_never_its_on_enter(self, db, project_service):
        _both_projects(db, project_service)
        ws_notifications, websocket = self._connected()

        _wake(db, project_service, ws_notifications=ws_notifications)

        assert len(websocket.sent) == 1
        assert websocket.sent[0]["type"] == "notification"
        assert websocket.sent[0]["project_name"] == "watcher"
        assert websocket.sent[0]["state"]["key"] == "x"  # self-loop — the state itself never changes
        # The fired action's on-enter is a task of its own (see
        # tracking/actuators/on_enter_task.py), never part of this frame.
        assert "on-enter" not in websocket.sent[0]
        # The fired action has a trigger and no tracking_service was wired
        # in (defaults to "always auto-tracked") — filtered out of
        # manual_actions same as a live session's own state payload would.
        assert websocket.sent[0]["state"]["manual_actions"] == []

    def test_manual_actions_includes_the_triggered_action_when_auto_tracking_is_disabled(self, db, project_service):
        watcher_session = _both_projects(db, project_service)
        ws_notifications, websocket = self._connected()

        _wake(
            db, project_service, ws_notifications=ws_notifications,
            tracking_service=_FakeTrackingService({watcher_session["id"]}),
        )

        assert [a["name"] for a in websocket.sent[0]["state"]["manual_actions"]] == ["notice"]

    def test_nothing_is_pushed_when_the_self_loop_does_not_fire(self, db, project_service):
        _both_projects(db, project_service, observed_moved=False)
        ws_notifications, websocket = self._connected()

        _wake(db, project_service, ws_notifications=ws_notifications)

        assert websocket.sent == []

    def test_the_transition_is_applied_regardless_of_whether_anyone_is_connected_or_ws_is_wired_at_all(self, db, project_service):
        unconnected_session = _both_projects(db, project_service)
        _wake(db, project_service, ws_notifications=WsNotifications(auth_service=None))
        assert db.get_signals(unconnected_session["id"])[-1]["new_state"] == "x"

        # ws_notifications omitted entirely — same as before the parameter existed.
        _wake(db, project_service)
        assert db.get_signals(unconnected_session["id"])[-1]["new_state"] == "x"


def test_publishing_state_changed_wakes_up_every_observer_that_has_a_session(app_db):
    # File-backed, not the plain `db` fixture — JobQueue runs `work` on a
    # separate thread, and a second thread opening its own connection to a
    # ":memory:" database would get a distinct, empty one.
    db = app_db
    project_service = ProjectService(db)
    watcher_session = _both_projects(db, project_service)

    service = WakeupService(db, project_service, make_test_job_service(db), _actuator_factory(db))
    service.register()

    publish(StateChanged(username=USERNAME, project_id="observed", from_state="a", to_state="b"))

    for _ in range(200):
        if len(db.get_signals(watcher_session["id"])) > 0:
            break
        time.sleep(0.01)

    rows = db.get_signals(watcher_session["id"])
    assert len(rows) == 1
    assert rows[0]["old_state"] == "x"
    assert rows[0]["new_state"] == "x"


def test_a_user_with_no_session_in_the_observer_project_is_never_woken(app_db):
    db = app_db  # see the JobQueue test above for why
    project_service = ProjectService(db)
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    # No chat session created in "watcher" at all for this user.
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))

    service = WakeupService(db, project_service, make_test_job_service(db), _actuator_factory(db))
    service.register()

    publish(StateChanged(username=USERNAME, project_id="observed", from_state="a", to_state="b"))
    time.sleep(0.1)

    # The real assertion is that this doesn't raise/log an exception;
    # get_observers still resolves "watcher", but the "has a session"
    # guard skips it.
    assert db.get_observers("observed") == ["watcher"]
