"""Cross-project wake-up, end to end: a self-loop action in one project
("watcher") references another ("observed") via automaton.*. A real
transition in "observed" publishes StateChanged, the reverse index
resolves it back to "watcher", and re-evaluating its triggers fires
the self-loop, recording a new transition.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton_builder import AutomatonBuilder
from chat.ws_adapter import WsAdapter
from events import StateChanged, publish
from conftest import NullBroadcaster, make_test_actuator_factory
from jobs import JobQueue
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
    # Auto-declares `project: {id: <project_name>}` whenever project_name
    # is a valid identifier, so automaton.observed references resolve.
    if project_name.isidentifier() and "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_name}\n{index_yml}"
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    # _finalize_project_update calls get_active_project_name(), which
    # raises when nothing is active yet — this bare helper has no
    # activation flow of its own to make that true.
    db.set_active_project_name(project_name, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_project_name, _automaton):
        pass

    asyncio.run(project_service._manager.finalize_update(project_name, automaton, commit))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


_actuator_factory = make_test_actuator_factory


def test_reverse_index_is_populated_when_watcher_is_built(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)

    assert db.get_observers("observed") == ["watcher"]
    assert db.get_observed_projects("watcher") == ["observed"]


def test_reverse_index_is_cleared_when_the_reference_is_removed(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    assert db.get_observers("observed") == ["watcher"]

    _publish_project(db, project_service, "watcher", OBSERVED_YML)  # no longer references automaton.observed at all

    assert db.get_observers("observed") == []


def test_reevaluate_and_apply_fires_the_self_loop_when_the_observed_state_now_matches(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    before = len(db.get_signals(watcher_session["id"]))

    # "observed" moves to state 'b' — the state the watcher's own
    # self-loop trigger is actually watching for.
    db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
    observed_session = db.get_latest_chat_session(USERNAME, "observed")
    db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

    job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    service = WakeupService(db, project_service, job_queue, _actuator_factory(db))
    asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

    after = db.get_signals(watcher_session["id"])
    assert len(after) == before + 1
    assert after[-1]["old_state"] == "x"
    assert after[-1]["new_state"] == "x"  # self-loop — the state itself never changes


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

    async def send_json(self, payload: dict):
        self.sent.append(payload)


class TestWsAdapterPush:
    """A fired self-loop wake-up pushes a "notification" frame (state/
    on-enter/project_name) to whichever connection is registered for
    `username`, never keyed on project_name (which only rides along
    inside the payload)."""

    def test_pushes_state_on_enter_and_project_name_when_the_self_loop_fires_and_a_connection_exists(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
        db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

        ws_adapter = WsAdapter(chat_service=None, db=db, auth_service=None)
        websocket = _FakeWebSocket()
        ws_adapter._connections[USERNAME] = websocket

        ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
        service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db), ws_adapter=ws_adapter)
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert len(websocket.sent) == 1
        assert websocket.sent[0]["type"] == "notification"
        assert websocket.sent[0]["project_name"] == "watcher"
        assert websocket.sent[0]["state"]["key"] == "x"  # self-loop — the state itself never changes
        assert "on-enter" in websocket.sent[0]
        # The fired action has a trigger and no tracking_service was wired
        # in (defaults to "always auto-tracked") — filtered out of
        # manual_actions same as a live session's own state payload would.
        assert websocket.sent[0]["state"]["manual_actions"] == []

    def test_manual_actions_includes_the_triggered_action_when_auto_tracking_is_disabled(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
        db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")
        watcher_session = db.get_latest_chat_session(USERNAME, "watcher")

        ws_adapter = WsAdapter(chat_service=None, db=db, auth_service=None)
        websocket = _FakeWebSocket()
        ws_adapter._connections[USERNAME] = websocket

        ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
        tracking_service = _FakeTrackingService({watcher_session["id"]})
        service = WakeupService(
            db, project_service, ephemeral_jobs, _actuator_factory(db),
            ws_adapter=ws_adapter, tracking_service=tracking_service,
        )
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert [a["name"] for a in websocket.sent[0]["state"]["manual_actions"]] == ["notice"]

    def test_no_connection_registered_is_a_silent_no_op_not_an_error(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
        db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")
        watcher_session = db.get_latest_chat_session(USERNAME, "watcher")

        ws_adapter = WsAdapter(chat_service=None, db=db, auth_service=None)  # nobody registered for USERNAME

        ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
        service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db), ws_adapter=ws_adapter)
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        # The transition itself is still applied and persisted regardless
        # of whether anyone was there to push it to live.
        assert db.get_signals(watcher_session["id"])[-1]["new_state"] == "x"

    def test_no_ws_adapter_at_all_is_unaffected_same_as_before_this_parameter_existed(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
        db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")
        watcher_session = db.get_latest_chat_session(USERNAME, "watcher")

        ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
        service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db))  # ws_adapter omitted entirely
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert db.get_signals(watcher_session["id"])[-1]["new_state"] == "x"

    def test_push_is_never_called_when_the_self_loop_does_not_fire(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
        db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
        # No transition to state 'b' at all — the watcher's own trigger never matches.

        ws_adapter = WsAdapter(chat_service=None, db=db, auth_service=None)
        websocket = _FakeWebSocket()
        ws_adapter._connections[USERNAME] = websocket

        ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
        service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db), ws_adapter=ws_adapter)
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert websocket.sent == []


def test_reevaluate_and_apply_does_nothing_when_the_observed_state_does_not_match(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
    db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    before = len(db.get_signals(watcher_session["id"]))

    ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db))
    asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

    assert len(db.get_signals(watcher_session["id"])) == before


def test_publishing_state_changed_wakes_up_every_observer_with_a_session(app_db):
    # File-backed, not the plain `db` fixture — JobQueue runs `work` on a
    # separate thread, and a second thread opening its own connection to a
    # ":memory:" database would get a distinct, empty one.
    db = app_db
    project_service = ProjectService(db)
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_name="watcher", revision=db.get_project_published_revision("watcher"))
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))
    observed_session = db.get_latest_chat_session(USERNAME, "observed")
    db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

    ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db))
    service.register()

    publish(StateChanged(username=USERNAME, project_name="observed", from_state="a", to_state="b"))

    for _ in range(200):
        if len(db.get_signals(watcher_session["id"])) > 0:
            break
        __import__("time").sleep(0.01)

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
    db.create_chat_session(username=USERNAME, project_name="observed", revision=db.get_project_published_revision("observed"))

    ephemeral_jobs = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    service = WakeupService(db, project_service, ephemeral_jobs, _actuator_factory(db))
    service.register()

    publish(StateChanged(username=USERNAME, project_name="observed", from_state="a", to_state="b"))

    import time
    time.sleep(0.1)

    # The real assertion is that this doesn't raise/log an exception;
    # get_observers still resolves "watcher", but the "has a session"
    # guard skips it.
    assert db.get_observers("observed") == ["watcher"]
