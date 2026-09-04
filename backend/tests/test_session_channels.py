"""Channel exclusivity for live sessions (phase 3): a live session
belongs to exactly one channel, closed and superseded only by a caller
with real intent (acquire_exclusive_session / "New session"), never by a
plain bootstrap (get_current_session_if_any_or_create_new).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from automaton.automaton import Action, Automaton, State
from chat.channels import NATIVE_CHAT, WHATSAPP_CHAT
from chat.chat_service import ChatService
from chat.errors import ChatServiceError
from chat.session_manager import ChatSessionManager
from chat.session_type_strategy import get_session_type_strategy
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from metrics.metric_service import MetricService
from session import Session
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.contract

LIVE = get_session_type_strategy('live')
PROJECT_ID = "channels-proj"
USERNAME = "user"
CHANNELS = (NATIVE_CHAT, WHATSAPP_CHAT)
EXISTING_STATES = ("same_channel_open", "other_channel_open", "expired", "closed", "absent")


def _other(channel: str) -> str:
    return WHATSAPP_CHAT if channel == NATIVE_CHAT else NATIVE_CHAT


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

    def apply_manual_action(self, action_name, session_id):
        automaton, state = self.get_active_automaton_and_state()
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        return automaton.get_state_payload(new_state), action, state.key


def _setup_project(db) -> None:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)


def _chat_service(db, *, session_manager: ChatSessionManager | None = None) -> ChatService:
    _setup_project(db)
    automaton = _automaton()
    ai_service = FakeAiService()
    project_service = _FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
        session_manager=session_manager or ChatSessionManager(db, open_window_minutes=5),
        tracking_service=tracking_service, metric_service=metric_service,
        job_service=job_service, actuator_factory=actuator_factory,
    )


def _make_open_session(db, channel: str, *, now=None) -> dict:
    now = now or datetime.utcnow()
    session_id = db.create_chat_session(
        USERNAME, PROJECT_ID, 0, datetime_start=now, datetime_end=now,
        start_state="a", end_state="a", type="live", channel=channel,
    )
    return db.get_chat_session(session_id)


def _make_expired_session(db, manager: ChatSessionManager, *, now=None) -> dict:
    now = now or datetime.utcnow()
    stale = now - manager.open_window - timedelta(seconds=1)
    session_id = db.create_chat_session(
        USERNAME, PROJECT_ID, 0, datetime_start=stale, datetime_end=stale,
        start_state="a", end_state="a", type="live", channel=NATIVE_CHAT,
    )
    return db.get_chat_session(session_id)


def _make_closed_session(db, *, now=None) -> dict:
    now = now or datetime.utcnow()
    session_id = db.create_chat_session(
        USERNAME, PROJECT_ID, 0, datetime_start=now, datetime_end=now,
        start_state="a", end_state="a", type="live", channel=NATIVE_CHAT,
    )
    db.close_chat_session(session_id, now, "manual-user")
    return db.get_chat_session(session_id)


def _build_existing(db, manager: ChatSessionManager, channel: str, state_name: str) -> dict | None:
    if state_name == "same_channel_open":
        return _make_open_session(db, channel)
    if state_name == "other_channel_open":
        return _make_open_session(db, _other(channel))
    if state_name == "expired":
        return _make_expired_session(db, manager)
    if state_name == "closed":
        return _make_closed_session(db)
    assert state_name == "absent"
    return None


# -- get_current_session_if_any_or_create_new (no intent) ------------------

@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
def test_get_current_session_if_any_or_create_new_matrix(db, channel, state_name):
    _setup_project(db)
    manager = ChatSessionManager(db, open_window_minutes=5)
    project_service = _FakeProjectService(_automaton())
    Session().channel = channel
    existing = _build_existing(db, manager, channel, state_name)

    result = manager.get_current_session_if_any_or_create_new(
        LIVE, project_service, USERNAME, PROJECT_ID, None, "a"
    )

    if state_name == "same_channel_open":
        assert result["id"] == existing["id"]
        assert result["channel"] == channel
        assert result["end_state"] == "a"
    elif state_name == "other_channel_open":
        assert result["id"] == existing["id"]
        assert result["channel"] == _other(channel)
        assert result["datetime_end"] == existing["datetime_end"]
        assert result["closed_at"] is None
    elif state_name == "expired":
        assert result["id"] != existing["id"]
        assert result["channel"] == channel
        reloaded = db.get_chat_session(existing["id"])
        assert reloaded["closed_at"] is None
    elif state_name == "closed":
        assert result["id"] != existing["id"]
        assert result["channel"] == channel
        reloaded = db.get_chat_session(existing["id"])
        assert reloaded["closed_at"] == existing["closed_at"]
        assert reloaded["channel"] == existing["channel"]
    else:
        assert result["channel"] == channel


# -- acquire_exclusive_session (real intent) --------------------------------

@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
def test_acquire_exclusive_session_matrix(db, channel, state_name):
    _setup_project(db)
    manager = ChatSessionManager(db, open_window_minutes=5)
    project_service = _FakeProjectService(_automaton())
    Session().channel = channel
    existing = _build_existing(db, manager, channel, state_name)

    result = manager.acquire_exclusive_session(LIVE, project_service, USERNAME, PROJECT_ID, "a")

    if state_name == "same_channel_open":
        assert result["id"] == existing["id"]
        assert result["channel"] == channel
        assert result["end_state"] == "a"
        assert result["closed_at"] is None
    elif state_name == "other_channel_open":
        assert result["id"] != existing["id"]
        assert result["channel"] == channel
        reloaded = db.get_chat_session(existing["id"])
        assert reloaded["closed_at"] is not None
        assert reloaded["close_reason"] == "channel-switch"
        assert reloaded["channel"] == _other(channel)
        assert reloaded["datetime_end"] == existing["datetime_end"]
    elif state_name == "expired":
        assert result["id"] != existing["id"]
        reloaded = db.get_chat_session(existing["id"])
        assert reloaded["closed_at"] is None
    elif state_name == "closed":
        assert result["id"] != existing["id"]
        reloaded = db.get_chat_session(existing["id"])
        assert reloaded["closed_at"] == existing["closed_at"]
        assert reloaded["close_reason"] == existing["close_reason"]
    else:
        assert result["channel"] == channel


# -- ChatService.create_session ("New session") -----------------------------

@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
async def test_create_session_matrix(db, channel, state_name):
    _setup_project(db)
    manager = ChatSessionManager(db, open_window_minutes=5)
    Session().channel = channel
    existing = _build_existing(db, manager, channel, state_name)
    chat_service = _chat_service(db, session_manager=manager)

    payload = await chat_service.create_session()

    assert existing is None or payload["id"] != existing["id"]
    assert payload["channel"] == channel
    if existing is None:
        return
    reloaded = db.get_chat_session(existing["id"])
    if state_name == "same_channel_open":
        assert reloaded["closed_at"] is not None
        assert reloaded["close_reason"] == "force-new-session"
    elif state_name == "other_channel_open":
        assert reloaded["closed_at"] is not None
        assert reloaded["close_reason"] == "channel-switch"
    elif state_name == "expired":
        assert reloaded["closed_at"] is None
    elif state_name == "closed":
        assert reloaded["closed_at"] == existing["closed_at"]
        assert reloaded["close_reason"] == existing["close_reason"]


# -- The shared admission gate, via process_turn/apply_manual_action -------

_REJECTION_MESSAGES = {
    "other_channel_open": "Session is not active.",
    "expired": "Session is not active.",
    "closed": "Session is closed.",
}


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
async def test_process_turn_with_explicit_session_id_matrix(db, channel, state_name):
    _setup_project(db)
    manager = ChatSessionManager(db, open_window_minutes=5)
    Session().channel = channel
    existing = _build_existing(db, manager, channel, state_name)
    chat_service = _chat_service(db, session_manager=manager)

    if state_name == "absent":
        with pytest.raises(ChatServiceError, match="Session not found."):
            await chat_service.process_turn(999999, "hi")
        return
    if state_name == "same_channel_open":
        result = await chat_service.process_turn(existing["id"], "hi")
        assert result["session_id"] == existing["id"]
        return
    with pytest.raises(ChatServiceError, match=_REJECTION_MESSAGES[state_name]):
        await chat_service.process_turn(existing["id"], "hi")


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
async def test_apply_manual_action_matrix(db, channel, state_name):
    _setup_project(db)
    manager = ChatSessionManager(db, open_window_minutes=5)
    Session().channel = channel
    existing = _build_existing(db, manager, channel, state_name)
    chat_service = _chat_service(db, session_manager=manager)

    if state_name == "absent":
        with pytest.raises(ChatServiceError, match="Session not found."):
            await chat_service.apply_manual_action("go", 999999)
        return
    if state_name == "same_channel_open":
        result = await chat_service.apply_manual_action("go", existing["id"])
        assert result["session_id"] == existing["id"]
        return
    with pytest.raises(ChatServiceError, match=_REJECTION_MESSAGES[state_name]):
        await chat_service.apply_manual_action("go", existing["id"])


# -- End to end: takeover in both directions --------------------------------

async def test_takeover_whatsapp_to_web_via_new_session_then_open_if_needed(db):
    """WhatsApp starts a session; the web calls "New session" while it's
    still open, taking it over — the fresh web session is genuinely new,
    so open_if_needed's own AI bootstrap fires for it."""
    chat_service = _chat_service(db)
    Session().channel = WHATSAPP_CHAT
    whatsapp_session = await chat_service.acquire_exclusive_session()

    Session().channel = NATIVE_CHAT
    web_payload = await chat_service.create_session()

    assert web_payload["id"] != whatsapp_session["id"]
    assert web_payload["channel"] == NATIVE_CHAT
    closed = db.get_chat_session(whatsapp_session["id"])
    assert closed["closed_at"] is not None
    assert closed["close_reason"] == "channel-switch"

    await chat_service.open_if_needed(web_payload["id"])
    assert db.get_messages(web_payload["id"]) != []


async def test_takeover_web_to_whatsapp_via_run_turn_then_prepare_user_initiated_turn(db):
    """The web has a session open; WhatsApp's own bootstrap
    (acquire_exclusive_session, standing in for _run_turn's own call)
    takes it over — prepare_user_initiated_turn never opens with an
    AI-initiated message of its own, unlike the takeover above."""
    chat_service = _chat_service(db)
    Session().channel = NATIVE_CHAT
    web_session = await chat_service.get_current_session_if_any_or_create_new(None)

    Session().channel = WHATSAPP_CHAT
    whatsapp_payload = await chat_service.acquire_exclusive_session()

    assert whatsapp_payload["id"] != web_session["id"]
    assert whatsapp_payload["channel"] == WHATSAPP_CHAT
    closed = db.get_chat_session(web_session["id"])
    assert closed["closed_at"] is not None
    assert closed["close_reason"] == "channel-switch"

    await chat_service.prepare_user_initiated_turn(whatsapp_payload["id"])
    assert db.get_messages(whatsapp_payload["id"]) == []
