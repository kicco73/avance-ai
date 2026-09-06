from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chat.session_manager import ChatSessionManager
from chat.session_type_strategy import get_session_type_strategy
from session import Session

LIVE = get_session_type_strategy('live')


class _FakeInitAction:
    def __init__(self, target):
        self.target = target


class _FakeAutomaton:
    def __init__(self, target):
        self.init_action = _FakeInitAction(target)


class _FakeState:
    def __init__(self, key):
        self.key = key


class _FakeProjectService:
    def __init__(self):
        self.state_key = "start"

    def get_active_automaton_and_state(self, username=None):
        return _FakeAutomaton(self.state_key), _FakeState(self.state_key)

    def get_automaton_and_state(self, project_name, type='live', username=None):
        return _FakeAutomaton(self.state_key), _FakeState(self.state_key)

    def get_published_revision(self, project_name):
        return 1

    def get_draft_revision(self, project_name):
        return 1


@pytest.fixture
def project_service() -> _FakeProjectService:
    return _FakeProjectService()


@pytest.fixture
def manager(db) -> ChatSessionManager:
    for project in ("proj", "proj-a", "proj-b"):
        db.ensure_project(project)
        db.publish_project(project)
    return ChatSessionManager(db)


def _create(manager, project_service, username, project_name, current_state):
    project_service.state_key = current_state
    return manager.create_session(LIVE, project_service, username, project_name)


def _resolve_or_create(manager, project_service, username, project_name, session_id, current_state):
    project_service.state_key = current_state
    return manager.get_current_session_if_any_or_create_new(
        LIVE, project_service, username, project_name, session_id, current_state
    )


def _raw_session(db, type: str, at: datetime | None = None, revision: int = 0) -> int:
    at = at or datetime.utcnow()
    return db.create_chat_session(
        "user", "proj", revision, datetime_start=at, datetime_end=at,
        start_state="start", end_state="start", type=type,
    )


def _freeze_after(monkeypatch, session, manager):
    stale_now = session["datetime_end"] + manager.open_window + timedelta(seconds=1)

    class FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return stale_now

    monkeypatch.setattr("chat.session_manager.datetime", FrozenDatetime)


@pytest.mark.contract
def test_open_window_defaults_to_60_minutes_and_is_configurable(manager, db):
    assert manager.open_window == timedelta(minutes=60)
    assert ChatSessionManager(db, open_window_minutes=5).open_window == timedelta(minutes=5)


@pytest.mark.contract
def test_has_open_sessions_for_revision_counts_only_live_sessions_still_within_the_window(db):
    db.ensure_project("proj")
    db.publish_project("proj")
    manager = ChatSessionManager(db, open_window_minutes=5)

    _raw_session(db, "test")
    _raw_session(db, "imported")
    assert manager.has_open_sessions_for_revision("proj", 0) is False

    _raw_session(db, "live", datetime.utcnow() - timedelta(minutes=10))
    assert manager.has_open_sessions_for_revision("proj", 0) is False

    _raw_session(db, "live")
    assert manager.has_open_sessions_for_revision("proj", 0) is True


@pytest.mark.regression
def test_is_open_never_crashes_without_a_datetime_end_expires_test_sessions_after_five_minutes_and_is_false_for_imported(manager):
    assert manager.is_open({"datetime_end": None, "closed_at": None}) is False

    recent = datetime.utcnow() - timedelta(minutes=1)
    assert manager.is_open({"type": "test", "datetime_end": recent, "closed_at": None}) is True
    long_ago = datetime.utcnow() - timedelta(minutes=10)
    assert manager.is_open({"type": "test", "datetime_end": long_ago, "closed_at": None}) is False

    assert manager.is_open({"type": "imported", "datetime_end": datetime.utcnow(), "closed_at": None}) is False


@pytest.mark.contract
def test_a_new_session_is_created_when_none_exists_stamped_with_the_current_channel(manager, project_service):
    session = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    assert session["username"] == "user"
    assert session["project_id"] == "proj"
    assert session["start_state"] == "start"
    assert session["end_state"] == "start"
    assert session["channel"] == "native-chat"
    assert manager.is_open(session)

    Session().channel = "whatsapp-chat"
    try:
        whatsapp = _resolve_or_create(manager, project_service, "user", "proj-a", None, "start")
    finally:
        Session().channel = "native-chat"
    assert whatsapp["channel"] == "whatsapp-chat"


@pytest.mark.contract
def test_an_open_session_is_reused_with_its_end_state_refreshed_never_trusting_the_callers_session_id(manager, project_service):
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    second = _resolve_or_create(manager, project_service, "user", "proj", first["id"], "next")
    assert second["id"] == first["id"]
    assert second["start_state"] == "start"
    assert second["end_state"] == "next"

    # A caller passing a session_id that isn't the real current one (stale
    # cache, another tab already rotated it, ...) must still resolve to the
    # actual current session — never trusted for the decision itself.
    assert _resolve_or_create(manager, project_service, "user", "proj", 999999, "next")["id"] == first["id"]

    other_project = _resolve_or_create(manager, project_service, "user", "proj-a", None, "start")
    assert other_project["id"] != first["id"]
    assert _resolve_or_create(manager, project_service, "user", "proj-a", None, "next")["id"] == other_project["id"]


@pytest.mark.regression
def test_a_new_session_is_created_once_the_current_one_has_gone_idle(manager, project_service, monkeypatch):
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")
    _freeze_after(monkeypatch, first, manager)

    second = _resolve_or_create(manager, project_service, "user", "proj", first["id"], "start")

    assert second["id"] != first["id"]
    assert second["start_state"] == "start"


@pytest.mark.contract
def test_a_manual_new_session_supersedes_a_still_open_one_and_touch_refreshes_the_end_state(manager, project_service):
    """At most one writable session per user+project — an explicit "new
    session" action must immediately become that one, even though the
    previous session is still within its open window."""
    first = _resolve_or_create(manager, project_service, "user", "proj", None, "start")

    manual = _create(manager, project_service, "user", "proj", "start")
    assert manual["id"] != first["id"]
    assert _resolve_or_create(manager, project_service, "user", "proj", first["id"], "next")["id"] == manual["id"]

    touched = manager.touch_session(manual["id"], "next")
    assert touched["id"] == manual["id"]
    assert touched["end_state"] == "next"


@pytest.mark.contract
def test_require_active_session_accepts_and_touches_the_open_session_and_rejects_every_other(manager, project_service, db):
    session = _create(manager, project_service, "user", "proj", "start")
    theirs = _create(manager, project_service, "other-user", "proj", "start")
    other_project = _create(manager, project_service, "user", "proj-a", "start")
    imported = _raw_session(db, "imported", revision=1)

    result = manager.require_active_session("user", "proj", session["id"], "next")
    assert result["id"] == session["id"]
    assert result["end_state"] == "next"

    for project, session_id in [("proj", None), ("proj", 999999), ("proj", theirs["id"]), ("proj-b", other_project["id"]), ("proj", imported)]:
        with pytest.raises(ValueError):
            manager.require_active_session("user", project, session_id, "next")


@pytest.mark.contract
def test_require_active_session_rejects_an_idle_session_without_rotating_it(manager, project_service, db, monkeypatch):
    """Unlike get_current_session_if_any_or_create_new, a closed session is
    never silently swapped for a new one here — the caller must bootstrap
    or start a new session explicitly instead."""
    session = _create(manager, project_service, "user", "proj", "start")
    _freeze_after(monkeypatch, session, manager)

    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", session["id"], "start")

    assert db.get_chat_session(session["id"])["end_state"] == "start"


@pytest.mark.regression
def test_the_active_session_is_the_most_recently_started_open_one_and_a_superseded_one_is_read_only(manager, project_service):
    """An older session that is still individually open (not expired)
    must not be usable for writes once a newer one has superseded it."""
    assert manager.get_active_session("user", "proj") is None

    older = _create(manager, project_service, "user", "proj", "start")
    newer = _create(manager, project_service, "user", "proj", "start")
    assert manager.get_active_session("user", "proj")["id"] == newer["id"]
    assert manager.is_open(older)

    with pytest.raises(ValueError):
        manager.require_active_session("user", "proj", older["id"], "next")
    assert manager.require_active_session("user", "proj", newer["id"], "next")["id"] == newer["id"]


@pytest.mark.contract
def test_closing_a_session_makes_it_neither_open_nor_active_nor_writable_idempotently_and_without_touching_its_end(manager, project_service, db):
    session = _create(manager, project_service, "user", "proj", "start")

    closed = manager.close_session(session, "manual-user")

    assert manager.is_open(closed) is False
    assert manager.get_active_session("user", "proj") is None
    assert closed["datetime_end"] == session["datetime_end"]
    assert closed["end_state"] == session["end_state"]
    assert closed["closed_at"] is not None
    assert closed["close_reason"] == "manual-user"
    with pytest.raises(ValueError, match="Session is closed."):
        manager.require_active_session("user", "proj", session["id"], "next")

    again = manager.close_session(closed, "force-new-session")
    assert again == closed
    assert again["close_reason"] == "manual-user"

    manager.touch_session(session["id"], "next")
    reloaded = db.get_chat_session(session["id"])
    assert reloaded["end_state"] == session["end_state"]
    assert reloaded["datetime_end"] == session["datetime_end"]

    test_session = db.get_chat_session(_raw_session(db, "test", revision=1))
    assert manager.is_open(manager.close_session(test_session, "manual-user")) is False


@pytest.mark.contract
def test_close_session_deletes_its_own_source_read_cache_but_nothing_else(manager, project_service, db):
    session = _create(manager, project_service, "user", "proj", "start")
    session_id = session["id"]
    db.write_archive_at_revision("proj", f"cache/sessions/{session_id}/sources/pino.csv", 0, b"cached", "text/csv")
    db.write_archive_at_revision("proj", "cache/sessions/999/sources/pino.csv", 0, b"other session", "text/csv")
    db.write_archive_at_revision("proj", "sources/pino.csv", 0, b"canonical", "text/csv")

    manager.close_session(session, "manual-user")

    names = db.list_archives("proj", revision=0)
    assert f"cache/sessions/{session_id}/sources/pino.csv" not in names
    assert "cache/sessions/999/sources/pino.csv" in names
    assert "sources/pino.csv" in names
