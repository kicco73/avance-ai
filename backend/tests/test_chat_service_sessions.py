from __future__ import annotations

from datetime import datetime

import pytest

from chat.chat_service import ChatService, ChatServiceError
from chat.session_manager import ChatSessionManager
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService


@pytest.fixture
def chat_service(db):
    # ai_service/project_service are never touched by the paths under test
    # here: _require_own_session raises before either would be used —
    # tracking_service's/metric_service's own get_active_automaton/
    # get_active_project_name likewise never fire.
    metric_service = MetricService(
        db, get_username=lambda: "user", get_active_project_name=lambda: None,
    )
    tracking_service = TrackingService(
        db, ai_service=None, metric_service=metric_service, get_active_automaton=lambda: None,
        get_username=lambda: "user", get_active_project_name=lambda: None,
    )
    return ChatService(
        ai_service=None, project_service=None, db=db, session_manager=ChatSessionManager(db),
        tracking_service=tracking_service, metric_service=metric_service,
    )


async def test_get_messages_raises_for_unknown_session(chat_service):
    with pytest.raises(ChatServiceError):
        await chat_service.get_messages(999999)


async def test_get_messages_raises_for_a_deleted_session(chat_service, db):
    """The exact regression this guards against: a session_id that was
    valid when the client last saw it, but has since been deleted (see
    delete_session) — get_messages must fail clean (404) instead of
    reaching open_if_needed's save_message and hitting a FOREIGN KEY
    IntegrityError."""
    session_id = db.create_chat_session(
        username="user",
        project_name="proj",
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )
    db.delete_chat_session(session_id)

    with pytest.raises(ChatServiceError):
        await chat_service.get_messages(session_id)


async def test_get_messages_raises_for_someone_elses_session(chat_service, db):
    session_id = db.create_chat_session(
        username="other-user",
        project_name="proj",
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )

    with pytest.raises(ChatServiceError):
        await chat_service.get_messages(session_id)


def test_delete_session_raises_for_unknown_session(chat_service):
    with pytest.raises(ChatServiceError):
        chat_service.delete_session(999999)


def test_delete_session_raises_for_someone_elses_session(chat_service, db):
    session_id = db.create_chat_session(
        username="other-user",
        project_name="proj",
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )

    with pytest.raises(ChatServiceError):
        chat_service.delete_session(session_id)

    # Untouched — the guard must reject before any delete happens.
    assert db.get_chat_session(session_id) is not None
