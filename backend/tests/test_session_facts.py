"""Tests for tracking.session_facts.SessionFacts — the `session`
namespace a trigger/`env:` expression resolves against. Derived fresh
from the current user+project's session/transition history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "proj"


@pytest.fixture
def session_facts(db) -> SessionFacts:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    return SessionFacts(db, FixedProjectContext(project_id=PROJECT_ID))


def _session(db, start, end=None) -> int:
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=start, datetime_end=end or start,
        start_state="a", end_state="a",
    )


def test_number_of_user_sessions_counts_every_session_of_this_user_and_project(db, session_facts):
    assert session_facts.number_of_user_sessions() == 0

    _session(db, datetime(2026, 1, 1))
    assert session_facts.number_of_user_sessions() == 1

    _session(db, datetime(2026, 1, 2))
    assert session_facts.number_of_user_sessions() == 2


def test_current_session_duration_is_zero_without_a_session_and_otherwise_measured_from_the_most_recent_one(db, session_facts):
    assert session_facts.current_session_duration_in_minutes() == 0.0

    ten_minutes_ago = datetime.utcnow() - timedelta(minutes=10)
    _session(db, ten_minutes_ago)

    assert 9.5 <= session_facts.current_session_duration_in_minutes() <= 10.5


def test_last_user_session_datetime_is_none_for_a_first_ever_session_and_the_previous_ones_start_afterwards(db, session_facts):
    _session(db, datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 9, 30))
    assert session_facts.last_user_session_datetime() is None

    _session(db, datetime(2026, 1, 2, 9, 0), datetime(2026, 1, 2, 9, 30))

    last = session_facts.last_user_session_datetime()
    assert last is not None
    if isinstance(last, str):
        last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    if last.tzinfo is not None:
        last = last.astimezone(timezone.utc).replace(tzinfo=None)
    assert last == datetime(2026, 1, 1, 9, 0)


def test_state_duration_is_zero_until_a_real_transition_then_measured_from_it(db, session_facts):
    assert session_facts.state_duration_in_minutes() == 0.0

    session_id = _session(db, datetime(2026, 1, 1))
    db.save_transition("a", "advance", "b", session_id, transition_log_level="WARNING")
    # save_transition always timestamps "now" — backdate it directly to
    # make the duration deterministic for the assertion below.
    from db.models import Tracking as TrackingModel
    TrackingModel.update(timestamp=datetime.utcnow() - timedelta(minutes=30)).where(
        TrackingModel.session == session_id
    ).execute()

    assert 29.5 <= session_facts.state_duration_in_minutes() <= 30.5
