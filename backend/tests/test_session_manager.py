from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chat.session_manager import ChatSessionManager, OPEN_WINDOW


@pytest.fixture
def manager(db) -> ChatSessionManager:
    return ChatSessionManager(db)


def test_creates_a_new_session_when_none_exists(manager):
    session = manager.get_or_create_current_session("user", "proj", None, "start")

    assert session["username"] == "user"
    assert session["project_name"] == "proj"
    assert session["start_state"] == "start"
    assert session["end_state"] == "start"
    assert manager.is_open(session)


def test_reuses_open_session_and_refreshes_end_state(manager):
    first = manager.get_or_create_current_session("user", "proj", None, "start")

    second = manager.get_or_create_current_session("user", "proj", first["id"], "next")

    assert second["id"] == first["id"]
    assert second["start_state"] == "start"
    assert second["end_state"] == "next"


def test_ignores_a_stale_or_unknown_session_id_from_the_caller(manager):
    first = manager.get_or_create_current_session("user", "proj", None, "start")

    # A caller passing a session_id that isn't the real current one (stale
    # cache, another tab already rotated it, ...) must still resolve to the
    # actual current session — never trusted for the decision itself.
    second = manager.get_or_create_current_session("user", "proj", 999999, "next")

    assert second["id"] == first["id"]


def test_creates_a_new_session_once_the_current_one_has_gone_idle(manager, monkeypatch):
    first = manager.get_or_create_current_session("user", "proj", None, "start")
    stale_now = first["datetime_end"] + OPEN_WINDOW + timedelta(seconds=1)

    class FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return stale_now

    monkeypatch.setattr("chat.session_manager.datetime", FrozenDatetime)

    second = manager.get_or_create_current_session("user", "proj", first["id"], "start")

    assert second["id"] != first["id"]
    assert second["start_state"] == "start"


def test_manual_create_session_supersedes_a_still_open_one(manager):
    """The core constraint this manager enforces: at most one writable
    session per user+project, the one with the latest datetime_start. An
    explicit "new session" action must immediately become that one, even
    though the previous session is still within its open window."""
    first = manager.get_or_create_current_session("user", "proj", None, "start")

    manual = manager.create_session("user", "proj", "start")
    assert manual["id"] != first["id"]

    resolved = manager.get_or_create_current_session("user", "proj", first["id"], "next")
    assert resolved["id"] == manual["id"]


def test_sessions_for_different_projects_are_independent(manager):
    proj_a = manager.get_or_create_current_session("user", "proj-a", None, "start")
    proj_b = manager.get_or_create_current_session("user", "proj-b", None, "start")

    assert proj_a["id"] != proj_b["id"]
    assert manager.get_or_create_current_session("user", "proj-a", None, "next")["id"] == proj_a["id"]


def test_get_session_returns_none_for_unknown_id(manager):
    assert manager.get_session(999999) is None


def test_touch_session_refreshes_end_state(manager):
    session = manager.create_session("user", "proj", "start")

    touched = manager.touch_session(session["id"], "next")

    assert touched["id"] == session["id"]
    assert touched["end_state"] == "next"
