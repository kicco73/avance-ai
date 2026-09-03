"""ChatService's own per-(username, project_name) lock over the session
lifecycle (resolve/close/create — see _session_lifecycle_scope): two
concurrent callers for the same user+project must never race each other
into two open sessions. Forces the interleaving deterministically (same
general technique as test_resolve_session_run_race.py) by wrapping the
lock the two calls actually share, rather than relying on scheduling luck.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton import Action, Automaton, State
from chat.channels import NATIVE_CHAT, WHATSAPP_CHAT
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService, NullBroadcaster, make_test_actuator_factory
from jobs import JobQueue
from metrics.metric_service import MetricService
from session import Session
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.contract

PROJECT_ID = "lock-proj"
USERNAME = "user"


def _automaton() -> Automaton:
    action = Action(name="go", ui_label="Go", ui_button="Go", target="a")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class _FakeProjectService:
    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton

    def get_active_automaton_and_state(self, username=None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state(self, project_id, type='live', username=None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state_for_session(self, session_id):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_for_session(self, session_id):
        return self._automaton

    def get_active_project_id(self):
        return PROJECT_ID

    def get_published_revision(self, project_id):
        return 0

    def get_draft_revision(self, project_id):
        return 0

    def legal_terms_pending(self, username, project_id):
        return False

    def get_project_availability(self, project_id):
        return (False, None)


def _chat_service(db) -> ChatService:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = _FakeProjectService(_automaton())
    metric_service = MetricService(db, project_service)
    job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    actuator_factory = make_test_actuator_factory(db, job_queue)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
        session_manager=ChatSessionManager(db, open_window_minutes=5),
        tracking_service=tracking_service, metric_service=metric_service,
        job_queue=job_queue, actuator_factory=actuator_factory,
    )


class _PausableLock:
    """Wraps a real asyncio.Lock so a test can observe the exact moment a
    caller enters the critical section, and hold it there under control —
    forcing a second concurrent caller to genuinely block on the same
    lock instead of happening to run uncontended."""

    def __init__(self) -> None:
        self._real_lock = asyncio.Lock()
        self.on_enter: asyncio.Event | None = None
        self.hold: asyncio.Event | None = None

    async def __aenter__(self):
        await self._real_lock.acquire()
        if self.on_enter is not None:
            self.on_enter.set()
        if self.hold is not None:
            await self.hold.wait()
        return self

    async def __aexit__(self, *exc_info):
        self._real_lock.release()


def _install_pausable_lock(chat_service: ChatService) -> _PausableLock:
    lock = _PausableLock()
    chat_service._session_lifecycle_locks.get = lambda key: lock
    return lock


async def test_concurrent_acquire_exclusive_session_from_different_channels_serializes(db):
    chat_service = _chat_service(db)
    lock = _install_pausable_lock(chat_service)
    lock.on_enter = asyncio.Event()
    lock.hold = asyncio.Event()

    async def native_call():
        Session().channel = NATIVE_CHAT
        return await chat_service.acquire_exclusive_session()

    async def whatsapp_call():
        Session().channel = WHATSAPP_CHAT
        return await chat_service.acquire_exclusive_session()

    first = asyncio.create_task(native_call())
    await asyncio.wait_for(lock.on_enter.wait(), timeout=5.0)

    second = asyncio.create_task(whatsapp_call())
    await asyncio.sleep(0.05)
    assert not second.done(), "the second caller entered the critical section while the first still held the lock"

    lock.hold.set()
    native_result = await first
    whatsapp_result = await second

    assert native_result["id"] != whatsapp_result["id"]
    open_sessions = [s for s in db.list_chat_sessions(USERNAME, PROJECT_ID) if s["closed_at"] is None]
    closed_sessions = [s for s in db.list_chat_sessions(USERNAME, PROJECT_ID) if s["closed_at"] is not None]
    assert len(open_sessions) == 1
    assert open_sessions[0]["id"] == whatsapp_result["id"]
    assert len(closed_sessions) == 1
    assert closed_sessions[0]["id"] == native_result["id"]
    assert closed_sessions[0]["close_reason"] == "channel-switch"


async def test_get_current_session_concurrent_with_acquire_exclusive_session_never_double_creates(db):
    chat_service = _chat_service(db)
    lock = _install_pausable_lock(chat_service)
    lock.on_enter = asyncio.Event()
    lock.hold = asyncio.Event()

    async def bootstrap_call():
        Session().channel = NATIVE_CHAT
        return await chat_service.get_current_session_if_any_or_create_new(None)

    async def exclusive_call():
        Session().channel = NATIVE_CHAT
        return await chat_service.acquire_exclusive_session()

    first = asyncio.create_task(bootstrap_call())
    await asyncio.wait_for(lock.on_enter.wait(), timeout=5.0)

    second = asyncio.create_task(exclusive_call())
    await asyncio.sleep(0.05)
    assert not second.done(), "the second caller entered the critical section while the first still held the lock"

    lock.hold.set()
    bootstrap_result = await first
    exclusive_result = await second

    assert bootstrap_result["id"] == exclusive_result["id"]
    sessions = db.list_chat_sessions(USERNAME, PROJECT_ID)
    assert len(sessions) == 1
