from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chat.session_manager import ChatSessionManager
from chat.session_type_strategy import get_session_type_strategy
from project.project_service import ProjectService

LIVE = get_session_type_strategy('live')


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


@pytest.fixture
def manager(db) -> ChatSessionManager:
    db.ensure_project("proj")
    db.publish_project("proj")
    db.ensure_project("proj-a")
    db.publish_project("proj-a")
    db.ensure_project("proj-b")
    db.publish_project("proj-b")
    return ChatSessionManager(db)


def _create(manager, project_service, username, project_name, current_state):
    return manager.create_session(LIVE, project_service, username, project_name, None, current_state)


def _resolve_or_create(manager, project_service, username, project_name, session_id, current_state):
    return manager.resolve_or_create_session(LIVE, project_service, username, project_name, session_id, None, current_state)


@pytest.mark.contract
def test_open_window_defaults_to_60_minutes(manager):
    assert manager.open_window == timedelta(minutes=60)


@pytest.mark.contract
def test_open_window_is_configurable(db):
    manager = ChatSessionManager(db, open_window_minutes=5)
    assert manager.open_window == timedelta(minutes=5)


@pytest.mark.regression
def test_is_open_is_false_never_a_crash_for_a_session_with_no_datetime_end(manager):
    """Regression: an imported session always has datetime_end=None,
    which must not crash the is_open comparison."""
    session = {"datetime_end": None}
    assert manager.is_open(session) is False


@pytest.mark.regression
def test_is_open_never_expires_for_a_test_session(manager):
    long_ago = datetime.utcnow() - timedelta(days=365)
    session = {"type": "test", "datetime_end": long_ago}
    assert manager.is_open(session) is True


@pytest.mark.contract
def test_creates_a_new_session_when_none_exists(manager, project_service):
    session = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    assert session["username"] == "user"
    assert session["project_name"] == "proj"
    assert session["start_state"] == "start"
    assert session["end_state"] == "start"
    assert manager.is_open(session)


@pytest.mark.contract
def test_reuses_open_session_and_refreshes_end_state(manager, project_service):
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    second = _resolve_or_create(manager, project_service, "user", "proj", first["id"], "next")

    assert second["id"] == first["id"]
    assert second["start_state"] == "start"
    assert second["end_state"] == "next"


@pytest.mark.contract
def test_ignores_a_stale_or_unknown_session_id_from_the_caller(manager, project_service):
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    # A caller passing a session_id that isn't the real current one (stale
    # cache, another tab already rotated it, ...) must still resolve to the
    # actual current session — never trusted for the decision itself.
    second = _resolve_or_create(manager, project_service, "user", "proj", 999999, "next")

    assert second["id"] == first["id"]


@pytest.mark.regression
def test_creates_a_new_session_once_the_current_one_has_gone_idle(manager, project_service, monkeypatch):
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")
    stale_now = first["datetime_end"] + manager.open_window + timedelta(seconds=1)

    class FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return stale_now

    monkeypatch.setattr("chat.session_manager.datetime", FrozenDatetime)

    second = _resolve_or_create(manager, project_service, "user", "proj", first["id"], "start")

    assert second["id"] != first["id"]
    assert second["start_state"] == "start"


@pytest.mark.contract
def test_manual_create_session_supersedes_a_still_open_one(manager, project_service):
    """At most one writable session per user+project — an explicit "new
    session" action must immediately become that one, even though the
    previous session is still within its open window."""
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    manual = _create(manager, project_service, "user", "proj", "start")
    assert manual["id"] != first["id"]

    resolved = _resolve_or_create(manager, project_service, "user", "proj", first["id"], "next")
    assert resolved["id"] == manual["id"]


@pytest.mark.contract
def test_sessions_for_different_projects_are_independent(manager, project_service):
    proj_a = _resolve_or_create(manager, project_service, "user", "proj-a", None, "start")
    proj_b = _resolve_or_create(manager, project_service, "user", "proj-b", None, "start")

    assert proj_a["id"] != proj_b["id"]
    assert _resolve_or_create(manager, project_service, "user", "proj-a", None, "next")["id"] == proj_a["id"]


@pytest.mark.contract
def test_touch_session_refreshes_end_state(manager, project_service):
    session = _create(manager, project_service, "user", "proj", "start")

    touched = manager.touch_session(session["id"], "next")

    assert touched["id"] == session["id"]
    assert touched["end_state"] == "next"


@pytest.mark.contract
def test_require_active_session_accepts_and_touches_an_open_session(manager, project_service):
    session = _create(manager, project_service, "user", "proj", "start")

    result = manager.require_active_session("user", "proj", session["id"], "next")

    assert result["id"] == session["id"]
    assert result["end_state"] == "next"


@pytest.mark.contract
def test_require_active_session_rejects_none(manager):
    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", None, "start")


@pytest.mark.contract
def test_require_active_session_rejects_unknown_session(manager):
    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", 999999, "start")


@pytest.mark.contract
def test_require_active_session_rejects_someone_elses_session(manager, project_service):
    theirs = _create(manager, project_service, "other-user", "proj", "start")

    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", theirs["id"], "start")


@pytest.mark.contract
def test_require_active_session_rejects_a_different_projects_session(manager, project_service):
    session = _create(manager, project_service, "user", "proj-a", "start")

    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj-b", session["id"], "start")


@pytest.mark.contract
def test_require_active_session_rejects_a_closed_session_no_auto_rotation(manager, project_service, db, monkeypatch):
    """The behavior this reinforces: unlike resolve_or_create_session, a
    closed session is never silently swapped for a new one here — the
    caller must bootstrap or start a new session explicitly instead."""
    session = _create(manager, project_service, "user", "proj", "start")
    stale_now = session["datetime_end"] + manager.open_window + timedelta(seconds=1)

    class FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return stale_now

    monkeypatch.setattr("chat.session_manager.datetime", FrozenDatetime)

    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", session["id"], "start")

    # Rejected, not replaced — no new session should have appeared.
    assert db.get_chat_session(session["id"])["end_state"] == "start"


@pytest.mark.contract
def test_get_active_session_is_none_when_none_exist(manager):
    assert manager.get_active_session("user", "proj") is None


@pytest.mark.contract
def test_get_active_session_is_the_most_recently_started_open_one(manager, project_service):
    _create(manager, project_service, "user", "proj", "start")
    newer = _create(manager, project_service, "user", "proj", "start")

    active = manager.get_active_session("user", "proj")

    assert active["id"] == newer["id"]


@pytest.mark.regression
def test_require_active_session_rejects_an_open_but_superseded_session(manager, project_service):
    """An older session that is still individually open (not expired)
    must not be usable for writes once a newer one has superseded it."""
    older = _create(manager, project_service, "user", "proj", "start")
    newer = _create(manager, project_service, "user", "proj", "start")
    assert manager.is_open(older)  # not expired — this is the crux of the bug

    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", older["id"], "next")

    # The active (newer) one is unaffected and still works normally.
    result = manager.require_active_session("user", "proj", newer["id"], "next")
    assert result["id"] == newer["id"]
