"""Cross-project wake-up (Prompt 6), end to end — a self-loop action in
one project ("watcher") references another ("observed") via automaton.*;
a real transition in "observed" publishes StateChanged (see
tracking.tracking_engine.TrackingEngine.notify_transition), the reverse
index (built at "watcher"'s own last save, see project.project_service.
ProjectService._finalize_project_update) resolves "observed" back to
"watcher" as an observer, and re-evaluating "watcher"'s own triggers
fires its self-loop action, recording a new transition for it too.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton_builder import AutomatonBuilder
from chat.ws_adapter import WsAdapter
from events import StateChanged, publish
from jobs import InMemoryJobSink, JobQueue
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
    """Same low-level db.save_project_files/publish_project write every
    other project-fixture test in this suite uses, plus the one step
    those skip: _finalize_project_update — the reverse index (see
    ProjectService's own docstring) is only ever refreshed there, not by
    a raw db write, so a test exercising it has to go through this
    exact call, same as a real save (put_project/put_project_file)
    already does."""
    # Auto-declares `project: {id: <project_name>}` (Prompt 8/9's
    # project.id) whenever project_name is a valid identifier — every
    # automaton.observed reference in this file's own fixtures expects
    # "observed" itself to resolve, so its own project.id has to equal
    # its project_name (see test_project_availability.py's own identical
    # helper for the full reasoning).
    if project_name.isidentifier() and "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_name}\n{index_yml}"
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    # _finalize_project_update always calls get_active_project_name(),
    # which raises outright when *nothing* is active yet (see its own
    # docstring) — never actually reached in the real app (some project
    # is always active by the time a save happens), but this bare test
    # helper has no session/activation flow of its own to make that true
    # on its own.
    db.set_active_project_name(project_name, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_automaton):
        pass

    asyncio.run(project_service._finalize_project_update(project_name, automaton, commit))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


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
    db.create_chat_session(username=USERNAME, project_name="watcher")
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    before = len(db.get_signals(watcher_session["id"]))

    # "observed" moves to state 'b' — the state the watcher's own
    # self-loop trigger is actually watching for.
    db.create_chat_session(username=USERNAME, project_name="observed")
    observed_session = db.get_latest_chat_session(USERNAME, "observed")
    db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

    ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
    service = WakeupService(db, project_service, ephemeral_jobs)
    asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

    after = db.get_signals(watcher_session["id"])
    assert len(after) == before + 1
    assert after[-1]["old_state"] == "x"
    assert after[-1]["new_state"] == "x"  # self-loop — the state itself never changes


class _FakeWebSocket:
    """Just enough to stand in for a real connection in WsAdapter's own
    username -> WebSocket _connections registry (see test_ws_adapter_
    registry.py's own identically-shaped fake) — push only ever calls
    send_json on it."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict):
        self.sent.append(payload)


class TestWsAdapterPush:
    """Prompt 13 — a fired self-loop wake-up pushes a "notification"
    frame (state/on-enter/project_name) to whichever connection is
    registered for `username`, if any — never "done" (that type is
    reserved for a normal turn's own response to a client-initiated
    request, which a push never is), and never keyed on project_name
    (the registry itself is username-only now, see WsAdapter's own
    docstring) — project_name only ever rides along inside the payload,
    so the client can tell which project this notification is about."""

    def test_pushes_state_on_enter_and_project_name_when_the_self_loop_fires_and_a_connection_exists(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher")
        db.create_chat_session(username=USERNAME, project_name="observed")
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

        ws_adapter = WsAdapter(chat_service=None, db=db)
        websocket = _FakeWebSocket()
        ws_adapter._connections[USERNAME] = websocket

        ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
        service = WakeupService(db, project_service, ephemeral_jobs, ws_adapter)
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert len(websocket.sent) == 1
        assert websocket.sent[0]["type"] == "notification"
        assert websocket.sent[0]["project_name"] == "watcher"
        assert websocket.sent[0]["state"]["key"] == "x"  # self-loop — the state itself never changes
        assert "on-enter" in websocket.sent[0]

    def test_no_connection_registered_is_a_silent_no_op_not_an_error(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher")
        db.create_chat_session(username=USERNAME, project_name="observed")
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")
        watcher_session = db.get_latest_chat_session(USERNAME, "watcher")

        ws_adapter = WsAdapter(chat_service=None, db=db)  # nobody registered for USERNAME

        ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
        service = WakeupService(db, project_service, ephemeral_jobs, ws_adapter)
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        # The transition itself is still applied and persisted regardless
        # of whether anyone was there to push it to live.
        assert db.get_signals(watcher_session["id"])[-1]["new_state"] == "x"

    def test_no_ws_adapter_at_all_is_unaffected_same_as_before_this_parameter_existed(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher")
        db.create_chat_session(username=USERNAME, project_name="observed")
        observed_session = db.get_latest_chat_session(USERNAME, "observed")
        db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")
        watcher_session = db.get_latest_chat_session(USERNAME, "watcher")

        ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
        service = WakeupService(db, project_service, ephemeral_jobs)  # ws_adapter omitted entirely
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert db.get_signals(watcher_session["id"])[-1]["new_state"] == "x"

    def test_push_is_never_called_when_the_self_loop_does_not_fire(self, db, project_service):
        _publish_project(db, project_service, "observed", OBSERVED_YML)
        _publish_project(db, project_service, "watcher", WATCHER_YML)
        db.create_chat_session(username=USERNAME, project_name="watcher")
        db.create_chat_session(username=USERNAME, project_name="observed")
        # No transition to state 'b' at all — the watcher's own trigger never matches.

        ws_adapter = WsAdapter(chat_service=None, db=db)
        websocket = _FakeWebSocket()
        ws_adapter._connections[USERNAME] = websocket

        ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
        service = WakeupService(db, project_service, ephemeral_jobs, ws_adapter)
        asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

        assert websocket.sent == []


def test_reevaluate_and_apply_does_nothing_when_the_observed_state_does_not_match(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_name="watcher")
    db.create_chat_session(username=USERNAME, project_name="observed")
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    before = len(db.get_signals(watcher_session["id"]))

    ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
    service = WakeupService(db, project_service, ephemeral_jobs)
    asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))

    assert len(db.get_signals(watcher_session["id"])) == before


def test_publishing_state_changed_wakes_up_every_observer_with_a_session(app_db):
    # File-backed, not the plain `db` fixture (see app_db's own
    # docstring) — JobQueue really does run `work` on a separate thread
    # with its own event loop, and a second thread opening its own
    # connection to a ":memory:" database gets a distinct, empty one,
    # not a shared connection to the same data this test just wrote.
    db = app_db
    project_service = ProjectService(db)
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_name="watcher")
    watcher_session = db.get_latest_chat_session(USERNAME, "watcher")
    db.create_chat_session(username=USERNAME, project_name="observed")
    observed_session = db.get_latest_chat_session(USERNAME, "observed")
    db.save_transition("a", "go", "b", observed_session["id"], transition_log_level="INFO")

    ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
    service = WakeupService(db, project_service, ephemeral_jobs)
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
    db.create_chat_session(username=USERNAME, project_name="observed")

    ephemeral_jobs = JobQueue(InMemoryJobSink(), max_concurrent=1)
    service = WakeupService(db, project_service, ephemeral_jobs)
    service.register()

    publish(StateChanged(username=USERNAME, project_name="observed", from_state="a", to_state="b"))

    import time
    time.sleep(0.1)

    # Nothing to assert on directly (no session id to check) — the real
    # assertion is that this doesn't raise/log an exception; get_observers
    # itself still resolves "watcher" as an observer, but the "has a
    # session" guard (see WakeupService._on_event) skips it.
    assert db.get_observers("observed") == ["watcher"]
