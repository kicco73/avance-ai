from __future__ import annotations

from datetime import datetime

import pytest

from turn.turn_service import TurnService, TurnServiceError
from turn.sessions.session_manager import SessionManager
from conftest import make_test_scheduler_service
from metrics.metric_service import MetricService
from system.web_session import WebSession
from tracking.tracking_service import TrackingService
pytestmark = pytest.mark.regression


@pytest.fixture
def turn_service(db):
    metric_service = MetricService(db, project_service=None)
    scheduler_service = make_test_scheduler_service(db)
    tracking_service = TrackingService(
        db, project_service=None, metrics_service=metric_service, namespace_factory=None,
    )
    return TurnService(
        ai_service=None, ai_test_service=None, project_service=None, db=db, session_manager=SessionManager(db),
        tracking_service=tracking_service, metric_service=metric_service,
        scheduler_service=scheduler_service, namespace_factory=None,
    )


async def test_get_messages_raises_for_unknown_session(turn_service):
    with pytest.raises(TurnServiceError):
        turn_service.read_history(999999)


async def test_get_messages_raises_for_a_deleted_session(turn_service, db):
    """A session_id deleted after the client last saw it must fail clean
    (404) instead of reaching save_message and hitting a FOREIGN KEY
    IntegrityError."""
    db.ensure_project("proj")
    db.publish_project("proj")
    session_id = db.create_chat_session(
        username="user",
        project_id="proj",
        revision=db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )
    db.delete_chat_session(session_id)

    with pytest.raises(TurnServiceError):
        turn_service.read_history(session_id)


async def test_get_messages_raises_for_someone_elses_session(turn_service, db):
    WebSession().role = "user"
    db.ensure_project("proj")
    db.publish_project("proj")
    session_id = db.create_chat_session(
        username="other-user",
        project_id="proj",
        revision=db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )

    with pytest.raises(TurnServiceError):
        turn_service.read_history(session_id)


async def test_delete_session_raises_for_unknown_session(turn_service):
    with pytest.raises(TurnServiceError):
        await turn_service.delete_session(999999)


async def test_delete_session_raises_for_someone_elses_session(turn_service, db):
    WebSession().role = "user"
    db.ensure_project("proj")
    db.publish_project("proj")
    session_id = db.create_chat_session(
        username="other-user",
        project_id="proj",
        revision=db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0),
        datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start",
        end_state="start",
    )

    with pytest.raises(TurnServiceError):
        await turn_service.delete_session(session_id)

    assert db.get_chat_session(session_id) is not None
