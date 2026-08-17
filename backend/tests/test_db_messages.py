from __future__ import annotations

from datetime import datetime

import pytest

# Every test in this file checks the shape of what Db returns (dict keys,
# types, None for unknown ids) rather than a specific behavioral fact —
# all contract.
pytestmark = pytest.mark.contract


def _make_session(db, *, username="user", project_name="proj", start=datetime(2026, 1, 1)):
    return db.create_chat_session(
        username=username,
        project_name=project_name,
        datetime_start=start,
        datetime_end=start,
        start_state="a",
        end_state="a",
    )


def test_get_message_returns_the_full_row(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hello", session_id, audio_text="spoken")

    message = db.get_message(message_id)

    assert message["id"] == message_id
    assert message["role"] == "user"
    assert message["content"] == "hello"
    assert message["audio_text"] == "spoken"
    assert message["session_id"] == session_id
    assert isinstance(message["timestamp"], str)


def test_get_message_returns_none_for_an_unknown_id(db):
    assert db.get_message(999999) is None


def test_get_messages_includes_session_id(db):
    session_id = _make_session(db)
    db.save_message("user", "hi", session_id)

    rows = db.get_messages(session_id)

    assert len(rows) == 1
    assert rows[0]["session_id"] == session_id
