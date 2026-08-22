from __future__ import annotations

from datetime import datetime

import pytest

from chat.chat_service import ChatService, ChatServiceError
from chat.session_manager import ChatSessionManager
from jobs import JobQueue, PersistedJobSink
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService


# Every test verifies a stale/deleted/someone-else's session_id is
# rejected (404) before any write happens (ChatService._require_own_session/
# _require_active_session).
pytestmark = pytest.mark.regression


@pytest.fixture
def chat_service(db):
    # ai_service/project_service are never touched: _require_own_session
    # raises before either would be used.
    metric_service = MetricService(db, project_service=None)
    # None is fine here since these tests never reach any path that reads it.
    tracking_service = TrackingService(
        db, ai_service=None, project_service=None, metrics_service=metric_service,
    )
    return ChatService(
        ai_service=None, project_service=None, db=db, session_manager=ChatSessionManager(db),
        tracking_service=tracking_service, metric_service=metric_service,
        persisted_jobs=JobQueue(PersistedJobSink(db), max_concurrent=1),
    )


async def test_get_messages_raises_for_unknown_session(chat_service):
    with pytest.raises(ChatServiceError):
        await chat_service.get_messages(999999)


async def test_get_messages_raises_for_a_deleted_session(chat_service, db):
    """A session_id deleted after the client last saw it must fail clean
    (404) instead of reaching save_message and hitting a FOREIGN KEY
    IntegrityError."""
    db.ensure_project("proj")
    db.publish_project("proj")
    session_id = db.create_chat_session(
        username="user",
        project_name="proj",
        revision=db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )
    db.delete_chat_session(session_id)

    with pytest.raises(ChatServiceError):
        await chat_service.get_messages(session_id)


async def test_get_messages_raises_for_someone_elses_session(chat_service, db):
    db.ensure_project("proj")
    db.publish_project("proj")
    session_id = db.create_chat_session(
        username="other-user",
        project_name="proj",
        revision=db.get_project_published_revision("proj"),
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
    db.ensure_project("proj")
    db.publish_project("proj")
    session_id = db.create_chat_session(
        username="other-user",
        project_name="proj",
        revision=db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )

    with pytest.raises(ChatServiceError):
        chat_service.delete_session(session_id)

    # Untouched — the guard must reject before any delete happens.
    assert db.get_chat_session(session_id) is not None
