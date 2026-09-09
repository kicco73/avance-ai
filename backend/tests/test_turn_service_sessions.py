from __future__ import annotations

from datetime import datetime

import pytest

from turn.turn_service import TurnService, TurnServiceError
from turn.sessions.session_manager import SessionManager
from conftest import make_test_scheduler_service
from metrics.metric_service import MetricService
from session import Session
from tracking.tracking_service import TrackingService


# Every test verifies a stale/deleted/someone-else's session_id is
# rejected (404) before any write happens (TurnService._require_own_session/
# _require_active_session).
pytestmark = pytest.mark.regression


@pytest.fixture
def turn_service(db):
    # ai_service/project_service are never touched: _require_own_session
    # raises before either would be used.
    metric_service = MetricService(db, project_service=None)
    scheduler_service = make_test_scheduler_service(db)
    # None is fine here since these tests never reach any path that reads it.
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
        await turn_service.get_messages(999999)


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
        await turn_service.get_messages(session_id)


async def test_get_messages_raises_for_someone_elses_session(turn_service, db):
    # Only a plain "user" is denied — a supervisor owns every session (see
    # TurnService._owns_session), so this must downgrade the default fixture role.
    Session().role = "user"
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
        await turn_service.get_messages(session_id)


def test_delete_session_raises_for_unknown_session(turn_service):
    with pytest.raises(TurnServiceError):
        turn_service.delete_session(999999)


def test_delete_session_raises_for_someone_elses_session(turn_service, db):
    # Only a plain "user" is denied — a supervisor owns every session (see
    # TurnService._owns_session), so this must downgrade the default fixture role.
    Session().role = "user"
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
        turn_service.delete_session(session_id)

    # Untouched — the guard must reject before any delete happens.
    assert db.get_chat_session(session_id) is not None
