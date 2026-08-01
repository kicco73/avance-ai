from __future__ import annotations

from datetime import datetime


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
    assert message["expected_state"] is None
    assert isinstance(message["timestamp"], str)


def test_get_message_returns_none_for_an_unknown_id(db):
    assert db.get_message(999999) is None


def test_get_messages_includes_session_id_and_expected_state(db):
    session_id = _make_session(db)
    db.save_message("user", "hi", session_id)

    rows = db.get_messages(session_id)

    assert len(rows) == 1
    assert rows[0]["session_id"] == session_id
    assert rows[0]["expected_state"] is None


def test_new_messages_default_to_not_being_an_evaluation_point(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hi", session_id)

    assert db.get_message(message_id)["is_evaluation_point"] is False
    assert db.get_messages(session_id)[0]["is_evaluation_point"] is False


def test_link_signal_to_message_flips_is_evaluation_point_and_sets_the_fk(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hi", session_id)
    signal_row_id = db.save_signal_snapshot({"foo": 1}, session_id)

    db.link_signal_to_message(signal_row_id, message_id)

    assert db.get_message(message_id)["is_evaluation_point"] is True
    linked = db.get_signal_row_by_message(message_id)
    assert linked["id"] == signal_row_id


def test_get_signal_row_by_message_is_none_when_unlinked(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hi", session_id)
    assert db.get_signal_row_by_message(message_id) is None


def test_set_message_expected_state_sets_and_clears(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hi", session_id)

    db.set_message_expected_state(message_id, "b")
    assert db.get_message(message_id)["expected_state"] == "b"

    db.set_message_expected_state(message_id, None)
    assert db.get_message(message_id)["expected_state"] is None
