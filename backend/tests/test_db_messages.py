from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.contract


def _make_session(db, *, username="user", project_name="proj", start=datetime(2026, 1, 1)):
    db.ensure_project(project_name)
    db.publish_project(project_name)
    return db.create_chat_session(
        username=username,
        project_id=project_name,
        revision=db.get_project_published_revision(project_name),
        datetime_start=start,
        datetime_end=start,
        start_state="a",
        end_state="a",
    )


def test_get_message_returns_the_full_row(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hello", session_id, audio_text="spoken", reaction="supportive")

    message = db.get_message(message_id)

    assert message["id"] == message_id
    assert message["role"] == "user"
    assert message["content"] == "hello"
    assert message["audio_text"] == "spoken"
    assert message["reaction"] == "supportive"
    assert message["session_id"] == session_id
    assert isinstance(message["timestamp"], str)


def test_get_message_reaction_defaults_to_none(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hello", session_id)

    assert db.get_message(message_id)["reaction"] is None


def test_get_messages_includes_reaction(db):
    session_id = _make_session(db)
    db.save_message("assistant", "hi", session_id, reaction="supportive")

    rows = db.get_messages(session_id)

    assert rows[0]["reaction"] == "supportive"


def test_get_message_returns_none_for_an_unknown_id(db):
    assert db.get_message(999999) is None


def test_get_messages_includes_session_id(db):
    session_id = _make_session(db)
    db.save_message("user", "hi", session_id)

    rows = db.get_messages(session_id)

    assert len(rows) == 1
    assert rows[0]["session_id"] == session_id


def _seed_turns(db, session_id, token_costs, start=datetime(2026, 1, 1, 12, 0, 0)):
    for i, tokens in enumerate(token_costs):
        role = "user" if i % 2 == 0 else "assistant"
        db.save_message(role, f"msg{i}", session_id, tokens=tokens, timestamp=start + timedelta(minutes=i))


def test_get_turn_history_with_no_budget_matches_get_messages(db):
    session_id = _make_session(db)
    _seed_turns(db, session_id, [100, 50, 200, 80])

    assert db.get_turn_history(session_id, None, None) == db.get_messages(session_id)


def test_get_turn_history_keeps_only_the_latest_messages_fitting_the_budget(db):
    session_id = _make_session(db)
    _seed_turns(db, session_id, [100, 50, 200, 80, 300, 120])

    rows = db.get_turn_history(session_id, None, 200)

    assert [r["content"] for r in rows] == ["msg5"]


def test_get_turn_history_combines_since_and_budget_in_one_pass(db):
    session_id = _make_session(db)
    start = datetime(2026, 1, 1, 12, 0, 0)
    _seed_turns(db, session_id, [100, 50, 200, 80, 300, 120], start=start)

    rows = db.get_turn_history(session_id, start + timedelta(minutes=1), 100000)

    assert [r["content"] for r in rows] == ["msg2", "msg3", "msg4", "msg5"]


def test_get_turn_history_treats_unknown_cost_messages_as_free(db):
    session_id = _make_session(db)
    db.save_message("user", "no-tokens", session_id, timestamp=datetime(2026, 1, 1, 12, 0, 0))
    db.save_message("assistant", "no-tokens-reply", session_id, timestamp=datetime(2026, 1, 1, 12, 1, 0))

    rows = db.get_turn_history(session_id, None, 8000)

    assert [r["content"] for r in rows] == ["no-tokens", "no-tokens-reply"]
