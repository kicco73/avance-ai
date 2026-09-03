"""Tests for tracking.session_facts.SessionFacts — the `session`
namespace a trigger/`env:` expression resolves against. Derived fresh
from the current user+project's session/transition history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts

USERNAME = "user"
PROJECT_ID = "proj"


def _session_facts(db) -> SessionFacts:
    return SessionFacts(db, FixedProjectContext(project_id=PROJECT_ID))


@pytest.mark.regression
def test_number_of_user_sessions_counts_every_session_for_the_project(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    session_facts = _session_facts(db)
    assert session_facts.number_of_user_sessions() == 0

    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    assert session_facts.number_of_user_sessions() == 1

    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 2), datetime_end=datetime(2026, 1, 2),
        start_state="a", end_state="a",
    )
    assert session_facts.number_of_user_sessions() == 2


@pytest.mark.regression
def test_current_session_duration_in_minutes_is_zero_with_no_session(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    assert _session_facts(db).current_session_duration_in_minutes() == 0.0


@pytest.mark.regression
def test_current_session_duration_in_minutes_uses_the_most_recent_session(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    ten_minutes_ago = datetime.utcnow() - timedelta(minutes=10)
    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=ten_minutes_ago, datetime_end=ten_minutes_ago,
        start_state="a", end_state="a",
    )

    duration = _session_facts(db).current_session_duration_in_minutes()

    assert 9.5 <= duration <= 10.5


@pytest.mark.regression
def test_last_user_session_datetime_is_none_for_a_first_ever_session(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )

    assert _session_facts(db).last_user_session_datetime() is None


@pytest.mark.regression
def test_last_user_session_datetime_is_the_previous_sessions_start(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1, 9, 0), datetime_end=datetime(2026, 1, 1, 9, 30),
        start_state="a", end_state="a",
    )
    db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 2, 9, 0), datetime_end=datetime(2026, 1, 2, 9, 30),
        start_state="a", end_state="a",
    )

    last_session_datetime = _session_facts(db).last_user_session_datetime()
    assert last_session_datetime is not None

    if isinstance(last_session_datetime, str):
        last_session_datetime = datetime.fromisoformat(last_session_datetime.replace("Z", "+00:00"))

    if last_session_datetime.tzinfo is not None:
        last_session_datetime = last_session_datetime.astimezone(timezone.utc).replace(tzinfo=None)

    assert last_session_datetime == datetime(2026, 1, 1, 9, 0)


@pytest.mark.regression
def test_state_duration_in_minutes_is_zero_with_no_transition_yet(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    assert _session_facts(db).state_duration_in_minutes() == 0.0


@pytest.mark.regression
def test_state_duration_in_minutes_since_the_last_real_transition(db):
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, USERNAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="b",
    )
    thirty_minutes_ago = datetime.utcnow() - timedelta(minutes=30)
    db.save_transition("a", "advance", "b", session_id, transition_log_level="WARNING")
    # save_transition always timestamps "now" — backdate it directly to
    # make the duration deterministic for the assertion below.
    from db.models import Tracking as TrackingModel
    TrackingModel.update(timestamp=thirty_minutes_ago).where(TrackingModel.session == session_id).execute()

    duration = _session_facts(db).state_duration_in_minutes()

    assert 29.5 <= duration <= 30.5
