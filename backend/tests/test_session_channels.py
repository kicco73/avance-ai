"""Channel exclusivity for live sessions (phase 3): a live session
belongs to exactly one channel, closed and superseded only by a caller
with real intent (acquire_exclusive_session / "New session"), never by a
plain bootstrap (get_current_session_if_any_or_create_new).
"""
from __future__ import annotations

import contextvars
from datetime import datetime, timedelta

import pytest

from automaton.automaton import Action, Automaton, State
from turn.channels import CHANNELS
from turn.turn_service import TurnService
from turn.errors import TurnServiceError
from turn.sessions.session_manager import SessionManager
from turn.sessions.session_type_strategy import get_session_type_strategy
from conftest import FakeAiService, make_test_namespace_factory, make_test_scheduler_service
from metrics.metric_service import MetricService
from system.web_session import WebSession
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.contract

LIVE = get_session_type_strategy('live')
PROJECT_ID = "channels-proj"
USERNAME = "user"
EXISTING_STATES = ("same_channel_open", "other_channel_open", "expired", "closed", "absent")


def _other(channel: str) -> str:
    return "whatsapp" if channel == "webchat" else "webchat"


def _automaton() -> Automaton:
    action = Action(name="go", ui_label="Go", ui_button="Go", target="a")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
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

    def get_automaton(self, project_id, revision):
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


def _turn_service(db, *, session_manager: SessionManager | None = None) -> TurnService:
    _setup_project(db)
    automaton = _automaton()
    ai_service = FakeAiService()
    project_service = _FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    scheduler_service = make_test_scheduler_service(db)
    namespace_factory = make_test_namespace_factory(db, scheduler_service)
    tracking_service = TrackingService(db, project_service, metric_service, namespace_factory)
    return TurnService(
        ai_service=ai_service, ai_test_service=ai_service, project_service=project_service, db=db,
        session_manager=session_manager or SessionManager(db, open_window_minutes=5),
        tracking_service=tracking_service, metric_service=metric_service,
        scheduler_service=scheduler_service, namespace_factory=namespace_factory,
    )


def _make_open_session(db, channel: str, *, now=None) -> dict:
    now = now or datetime.utcnow()
    session_id = db.create_chat_session(
        USERNAME, PROJECT_ID, 0, datetime_start=now, datetime_end=now,
        start_state="a", end_state="a", type="live", channel=channel,
    )
    return db.get_chat_session(session_id)


def _make_expired_session(db, manager: SessionManager, *, now=None) -> dict:
    now = now or datetime.utcnow()
    stale = now - manager.open_window - timedelta(seconds=1)
    session_id = db.create_chat_session(
        USERNAME, PROJECT_ID, 0, datetime_start=stale, datetime_end=stale,
        start_state="a", end_state="a", type="live", channel="webchat",
    )
    return db.get_chat_session(session_id)


def _make_closed_session(db, *, now=None) -> dict:
    now = now or datetime.utcnow()
    session_id = db.create_chat_session(
        USERNAME, PROJECT_ID, 0, datetime_start=now, datetime_end=now,
        start_state="a", end_state="a", type="live", channel="webchat",
    )
    db.close_chat_session(session_id, now, "manual-user")
    return db.get_chat_session(session_id)


def _build_existing(db, manager: SessionManager, channel: str, state_name: str) -> dict | None:
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
    manager = SessionManager(db, open_window_minutes=5)
    project_service = _FakeProjectService(_automaton())
    WebSession().channel = channel
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
    manager = SessionManager(db, open_window_minutes=5)
    project_service = _FakeProjectService(_automaton())
    WebSession().channel = channel
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


# -- TurnService.create_session ("New session") -----------------------------

@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
async def test_create_session_matrix(db, channel, state_name):
    _setup_project(db)
    manager = SessionManager(db, open_window_minutes=5)
    WebSession().channel = channel
    existing = _build_existing(db, manager, channel, state_name)
    turn_service = _turn_service(db, session_manager=manager)

    payload = await turn_service.create_session()

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
    manager = SessionManager(db, open_window_minutes=5)
    WebSession().channel = channel
    existing = _build_existing(db, manager, channel, state_name)
    turn_service = _turn_service(db, session_manager=manager)

    if state_name == "absent":
        with pytest.raises(TurnServiceError, match="Session not found."):
            await turn_service.process_turn(999999, "hi")
        return
    if state_name == "same_channel_open":
        result = await turn_service.process_turn(existing["id"], "hi")
        assert result["session_id"] == existing["id"]
        return
    with pytest.raises(TurnServiceError, match=_REJECTION_MESSAGES[state_name]):
        await turn_service.process_turn(existing["id"], "hi")


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("state_name", EXISTING_STATES)
async def test_apply_manual_action_matrix(db, channel, state_name):
    _setup_project(db)
    manager = SessionManager(db, open_window_minutes=5)
    WebSession().channel = channel
    existing = _build_existing(db, manager, channel, state_name)
    turn_service = _turn_service(db, session_manager=manager)

    if state_name == "absent":
        with pytest.raises(TurnServiceError, match="Session not found."):
            await turn_service.apply_manual_action("go", 999999)
        return
    if state_name == "same_channel_open":
        result = await turn_service.apply_manual_action("go", existing["id"])
        assert result["session_id"] == existing["id"]
        return
    with pytest.raises(TurnServiceError, match=_REJECTION_MESSAGES[state_name]):
        await turn_service.apply_manual_action("go", existing["id"])


# -- End to end: takeover in both directions --------------------------------

async def test_takeover_whatsapp_to_web_via_new_session_then_open_if_needed(db):
    """WhatsApp starts a session; the web calls "New session" while it's
    still open, taking it over — the fresh web session is genuinely new,
    so open_if_needed's own AI bootstrap fires for it."""
    turn_service = _turn_service(db)
    WebSession().channel = "whatsapp"
    whatsapp_session = await turn_service.acquire_exclusive_session()

    WebSession().channel = "webchat"
    web_payload = await turn_service.create_session()

    assert web_payload["id"] != whatsapp_session["id"]
    assert web_payload["channel"] == "webchat"
    closed = db.get_chat_session(whatsapp_session["id"])
    assert closed["closed_at"] is not None
    assert closed["close_reason"] == "channel-switch"

    await turn_service.open_if_needed(web_payload["id"])
    assert db.get_messages(web_payload["id"]) != []


async def test_takeover_web_to_whatsapp_via_run_turn_then_prepare_user_initiated_turn(db):
    """The web has a session open; WhatsApp's own bootstrap
    (acquire_exclusive_session, standing in for _run_turn's own call)
    takes it over — prepare_user_initiated_turn never opens with an
    AI-initiated message of its own, unlike the takeover above."""
    turn_service = _turn_service(db)
    WebSession().channel = "webchat"
    web_session = await turn_service.get_current_session_if_any_or_create_new(None)

    WebSession().channel = "whatsapp"
    whatsapp_payload = await turn_service.acquire_exclusive_session()

    assert whatsapp_payload["id"] != web_session["id"]
    assert whatsapp_payload["channel"] == "whatsapp"
    closed = db.get_chat_session(web_session["id"])
    assert closed["closed_at"] is not None
    assert closed["close_reason"] == "channel-switch"

    await turn_service.prepare_user_initiated_turn(whatsapp_payload["id"])
    assert db.get_messages(whatsapp_payload["id"]) == []


# -- Reporting vs. admitting: only one of the two is a channel question ------

def _without_a_channel(call):
    """Runs `call` in a brand-new context, where WebSession().channel was
    never set — what every caller looks like once auth/auth_middleware.py
    stops forging native-chat for each HTTP request. WebSession().channel
    raises there rather than defaulting, so any read on the way through
    fails loudly instead of quietly answering for somebody else."""
    context = contextvars.Context()

    def run():
        WebSession().user = USERNAME
        WebSession().role = "supervisor"
        return call()

    return context.run(run)


def test_reporting_on_a_session_never_asks_which_channel_the_caller_is_on(db):
    """Session listings, titles and comments are served to the editor,
    which is not a channel and has none to declare. They report `current`
    — is this the session its type's active slot holds — and `channel`,
    and leave writability to whoever is asking: a live session opened over
    WhatsApp is perfectly current and still not the chat window's to write
    to (see frontend sessionChannels.js's isWritableHere)."""
    _setup_project(db)
    manager = SessionManager(db, open_window_minutes=5)
    session = _make_open_session(db, "whatsapp")
    turn_service = _turn_service(db, session_manager=manager)

    listed = _without_a_channel(lambda: turn_service.list_sessions(PROJECT_ID))
    renamed = _without_a_channel(lambda: turn_service.set_session_title(session["id"], "Renamed"))

    assert [s["id"] for s in listed] == [session["id"]]
    assert listed[0]["current"] is True
    assert listed[0]["channel"] == "whatsapp"
    assert renamed["current"] is True
    assert renamed["channel"] == "whatsapp"


def test_admitting_a_write_still_refuses_a_caller_with_no_channel_at_all(db):
    """The other half, and the reason `channel` is an argument to
    is_valid_write_target rather than a read inside it: a write has to say
    who is speaking. A caller that cannot name its channel is not allowed
    to guess one."""
    _setup_project(db)
    manager = SessionManager(db, open_window_minutes=5)
    session = _make_open_session(db, "webchat")

    with pytest.raises(RuntimeError, match="outside a request context"):
        _without_a_channel(
            lambda: manager.require_active_session(USERNAME, PROJECT_ID, session["id"], "a")
        )


def test_a_live_session_with_no_channel_is_writable_from_nowhere(db):
    """Why SchemaMigrator._backfill_channel exists. A live session is
    admitted only by the channel that opened it, so one whose channel is
    NULL matches nobody — not WhatsApp, not the chat window, not a job.
    Every live session that predates the channel column would be exactly
    that if the migration left it NULL, which is what the column default
    used to prevent."""
    _setup_project(db)
    manager = SessionManager(db, open_window_minutes=5)
    session = _make_open_session(db, "webchat")
    # The row is edited straight through the model: create_chat_session
    # refuses to make one this way, which is the point.
    from db.models import CoreSession
    CoreSession.update(channel=None).where(CoreSession.id == session["id"]).execute()

    for channel in CHANNELS:
        WebSession().channel = channel
        with pytest.raises(ValueError, match="Session is not active."):
            manager.require_active_session(USERNAME, PROJECT_ID, session["id"], "a")
