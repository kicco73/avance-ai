from __future__ import annotations

from datetime import datetime


def _make_session(db, *, username="user", project_name="proj", start, end=None, start_state="start", end_state=None):
    end = end if end is not None else start
    end_state = end_state if end_state is not None else start_state
    return db.create_chat_session(
        username=username,
        project_name=project_name,
        datetime_start=start,
        datetime_end=end,
        start_state=start_state,
        end_state=end_state,
    )


def test_create_and_get_chat_session(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    session = db.get_chat_session(session_id)

    assert session["username"] == "user"
    assert session["project_name"] == "proj"
    assert session["start_state"] == "start"
    assert session["end_state"] == "start"


def test_get_chat_session_returns_none_for_unknown_id(db):
    assert db.get_chat_session(999999) is None


def test_get_latest_chat_session_picks_most_recent_start(db):
    _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    newer = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    latest = db.get_latest_chat_session("user", "proj")

    assert latest["id"] == newer


def test_get_latest_chat_session_scoped_by_username_and_project(db):
    _make_session(db, username="user", project_name="proj-a", start=datetime(2026, 1, 1, 12, 0, 0))

    assert db.get_latest_chat_session("user", "proj-b") is None
    assert db.get_latest_chat_session("other-user", "proj-a") is None


def test_touch_chat_session_updates_end_only(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    new_end = datetime(2026, 1, 1, 10, 30, 0)

    db.touch_chat_session(session_id, new_end, "next")
    session = db.get_chat_session(session_id)

    assert session["datetime_end"] == new_end
    assert session["end_state"] == "next"
    assert session["start_state"] == "start"


def test_save_message_requires_a_session(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_message("user", "hello", session_id)
    messages = db.get_messages(session_id)

    assert [m["content"] for m in messages] == ["hello"]


def test_get_messages_scoped_by_session_not_project(db):
    s1 = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    s2 = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "in session 1", s1)
    db.save_message("user", "in session 2", s2)

    assert [m["content"] for m in db.get_messages(s1)] == ["in session 1"]
    assert [m["content"] for m in db.get_messages(s2)] == ["in session 2"]


def test_reset_project_deletes_sessions_and_their_messages(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hello", session_id)

    db.reset_project("proj")

    assert db.get_chat_session(session_id) is None
    assert db.get_messages(session_id) == []
