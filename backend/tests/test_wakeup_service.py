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
from system.bus import UI_NOTIFICATION
from events import StateChanged, publish
from conftest import RecordedMessages, make_test_namespace_factory, make_test_scheduler_service
from turn.sessions.session_manager import SessionManager
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService
from tracking.wakeup_service import WakeupService

REACHES_INTO = {
    "_reevaluate_and_apply": "pre-existing; the public path needs the file-backed db fixture and a wait loop",
}

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
    if project_name.isidentifier() and "project:" not in index_yml:
        index_yml = f"project:\n  id: {project_name}\n  family: test\n{index_yml}"
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    db.set_active_project_id(project_name, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})


    asyncio.run(project_service.manager.finalize_update(project_name, automaton))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db, AutomatonLoader(db), SessionManager(db))


_namespace_factory = make_test_namespace_factory


class _FakeTrackingService:
    def __init__(self, disabled_session_ids):
        self._disabled = disabled_session_ids

    def is_auto_tracking_enabled(self, session_id):
        return session_id not in self._disabled




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
    service = WakeupService(db, project_service, make_test_scheduler_service(db), _namespace_factory(db), **kwargs)
    asyncio.run(service._reevaluate_and_apply(USERNAME, "watcher"))


def test_the_reverse_index_records_the_reference_and_is_cleared_once_it_is_removed(db, project_service):
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)

    assert db.get_observers("observed") == ["watcher"]
    assert db.get_observed_projects("watcher") == ["observed"]

    _publish_project(db, project_service, "watcher", OBSERVED_YML)

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
    assert after[-1]["new_state"] == "x"
    assert after[-1]["origin"] == "system"


class TestWakeupNotification:
    """A fired self-loop wake-up publishes a ui.notification (state/
    project_name) addressed to `username`, never keyed on project_id
    (which only rides along inside the body). The key is deliberately
    still "project_name", not "project_id": chatClient.js (frontend,
    off-limits) parses this exact message shape by that literal key name
    — see WakeupService._reevaluate_and_apply's own comment."""

    def test_a_fired_self_loop_announces_the_state_and_project_name_but_never_its_task(self, db, project_service):
        _both_projects(db, project_service)
        notified = RecordedMessages(UI_NOTIFICATION)

        _wake(db, project_service)

        (message,) = notified.for_user(USERNAME)
        assert message.body["project_name"] == "watcher"
        assert message.body["state"]["key"] == "x"
        assert "task" not in message.body
        assert message.body["buttons"] == []

    def test_the_choices_include_the_triggered_action_when_auto_tracking_is_disabled(self, db, project_service):
        watcher_session = _both_projects(db, project_service)
        notified = RecordedMessages(UI_NOTIFICATION)

        _wake(db, project_service, tracking_service=_FakeTrackingService({watcher_session["id"]}))

        (message,) = notified.for_user(USERNAME)
        assert [a["name"] for a in message.body["buttons"]] == ["notice"]

    def test_nothing_is_announced_when_the_self_loop_does_not_fire(self, db, project_service):
        _both_projects(db, project_service, observed_moved=False)
        notified = RecordedMessages(UI_NOTIFICATION)

        _wake(db, project_service)

        assert notified.messages == []

    def test_the_transition_is_applied_whether_or_not_anyone_is_listening(self, db, project_service):
        """The nudge is published either way; whether an interface is
        connected — or subscribed at all — is not this service's business."""
        unconnected_session = _both_projects(db, project_service)
        _wake(db, project_service)
        assert db.get_signals(unconnected_session["id"])[-1]["new_state"] == "x"

        _wake(db, project_service)
        assert db.get_signals(unconnected_session["id"])[-1]["new_state"] == "x"


def test_publishing_state_changed_wakes_up_every_observer_that_has_a_session(app_db):
    db = app_db
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    watcher_session = _both_projects(db, project_service)

    service = WakeupService(db, project_service, make_test_scheduler_service(db), _namespace_factory(db))
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
    db = app_db
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    _publish_project(db, project_service, "observed", OBSERVED_YML)
    _publish_project(db, project_service, "watcher", WATCHER_YML)
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))

    service = WakeupService(db, project_service, make_test_scheduler_service(db), _namespace_factory(db))
    service.register()

    publish(StateChanged(username=USERNAME, project_id="observed", from_state="a", to_state="b"))
    time.sleep(0.1)
    assert db.get_observers("observed") == ["watcher"]
